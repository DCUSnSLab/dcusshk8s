"""
Custom SFTP server for KubeSSH.

Proxies all file I/O operations to user Kubernetes pods via kubectl exec,
enabling students to use scp/sftp to transfer files to/from their pods.
"""

import asyncio
import io
import os
import stat
import time
import logging

import asyncssh
from asyncssh import SFTPServer, SFTPAttrs, SFTPName, SFTPError, \
    FXF_READ, FXF_WRITE, FXF_CREAT, FXF_TRUNC, FXF_APPEND, FXF_EXCL

import escapism
import string

logger = logging.getLogger('kubessh.sftp')



def _make_pod_name(username):
    """Convert SSH username to pod name, matching UserPod._pod_name_default logic."""
    safe_chars = set(string.ascii_lowercase + string.digits)
    safe_username = escapism.escape(username, safe=safe_chars, escape_char='-').lower()
    return f'ssh-{safe_username}'


class KubeSFTPFileHandle:
    """
    Represents an open file handle for SFTP operations.
    
    For reads: data is pre-fetched from the pod into a memory buffer.
    For writes: data is accumulated in a memory buffer and flushed on close.
    """
    def __init__(self, path, pod_name, namespace, is_read, is_write, is_append=False):
        self.path = path
        self.pod_name = pod_name
        self.namespace = namespace
        self.is_read = is_read
        self.is_write = is_write
        self.is_append = is_append
        self.buffer = io.BytesIO()
        self.closed = False
        self.pending_attrs = None  # Deferred attributes to apply on close


class KubeSFTPServer(SFTPServer):
    """
    SFTP server that proxies file operations to a user's Kubernetes pod.
    
    All file I/O is performed via kubectl exec commands targeting the
    user's pod in the configured namespace.
    """

    # Set by app.py before starting the server
    namespace = None

    def __init__(self, chan):
        super().__init__(chan)
        
        # Get the username from the SSH channel
        username = chan.get_extra_info('username')
        self._username = username
        self._pod_name = _make_pod_name(username)
        self._namespace = self.namespace
        
        logger.info(f'SFTP session started for user={username}, pod={self._pod_name}, namespace={self._namespace}')

    async def _kubectl_exec(self, command, stdin_data=None):
        """
        Execute a command inside the user's pod via kubectl exec.
        
        Returns (stdout_bytes, stderr_bytes, returncode).
        """
        kubectl_cmd = [
            'kubectl', 'exec',
            '--namespace', self._namespace,
            '-c', 'shell',
        ]
        
        if stdin_data is not None:
            kubectl_cmd.append('-i')
        
        kubectl_cmd += [self._pod_name, '--'] + command
        
        proc = await asyncio.create_subprocess_exec(
            *kubectl_cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await proc.communicate(input=stdin_data)
        return stdout, stderr, proc.returncode

    def _raise_if_error(self, stderr, returncode, operation=''):
        """Raise SFTPError if the kubectl command failed."""
        if returncode != 0:
            err_msg = stderr.decode('utf-8', errors='replace').strip()
            logger.warning(f'SFTP {operation} failed for pod={self._pod_name}: {err_msg}')
            
            if 'No such file or directory' in err_msg:
                raise SFTPError(asyncssh.FX_NO_SUCH_FILE, err_msg)
            elif 'Permission denied' in err_msg:
                raise SFTPError(asyncssh.FX_PERMISSION_DENIED, err_msg)
            elif 'File exists' in err_msg or 'already exists' in err_msg:
                raise SFTPError(asyncssh.FX_FAILURE, err_msg)
            elif 'Not a directory' in err_msg:
                raise SFTPError(asyncssh.FX_NO_SUCH_FILE, err_msg)
            elif 'Is a directory' in err_msg:
                raise SFTPError(asyncssh.FX_FAILURE, err_msg)
            else:
                raise SFTPError(asyncssh.FX_FAILURE, err_msg or f'{operation} failed')

    # ─── File access methods ───────────────────────────────────────────

    async def open(self, path, pflags, attrs):
        """Open a file on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        
        is_read = bool(pflags & FXF_READ) or not (pflags & FXF_WRITE)
        is_write = bool(pflags & FXF_WRITE)
        is_append = bool(pflags & FXF_APPEND)
        is_creat = bool(pflags & FXF_CREAT)
        is_trunc = bool(pflags & FXF_TRUNC)
        is_excl = bool(pflags & FXF_EXCL)
        
        handle = KubeSFTPFileHandle(
            path=path_str,
            pod_name=self._pod_name,
            namespace=self._namespace,
            is_read=is_read,
            is_write=is_write,
            is_append=is_append,
        )
        
        if is_write and is_excl:
            # Check if file already exists
            _, _, rc = await self._kubectl_exec(['test', '-e', path_str])
            if rc == 0:
                raise SFTPError(asyncssh.FX_FAILURE, 'File already exists')
        
        if is_write and is_creat:
            # Ensure parent directory exists, and touch the file
            parent_dir = os.path.dirname(path_str)
            if parent_dir:
                await self._kubectl_exec(['mkdir', '-p', parent_dir])
            if is_trunc or not is_read:
                # Will be written from scratch on close
                pass
        
        if is_read and not is_write:
            # Pre-fetch file content from pod
            stdout, stderr, rc = await self._kubectl_exec(['cat', path_str])
            if rc != 0:
                self._raise_if_error(stderr, rc, 'open/read')
            handle.buffer = io.BytesIO(stdout)
        elif is_read and is_write and not is_trunc:
            # Read existing content for read+write mode
            stdout, stderr, rc = await self._kubectl_exec(['cat', path_str])
            if rc == 0:
                handle.buffer = io.BytesIO(stdout)
            # If file doesn't exist and CREAT is set, start with empty buffer
        
        logger.debug(f'Opened file {path_str} (read={is_read}, write={is_write})')
        return handle

    async def close(self, file_obj):
        """Close an open file, flushing written data to the pod if needed."""
        if isinstance(file_obj, KubeSFTPFileHandle) and not file_obj.closed:
            if file_obj.is_write:
                data = file_obj.buffer.getvalue()
                
                if file_obj.is_append:
                    # Append mode: use tee -a
                    stdout, stderr, rc = await self._kubectl_exec(
                        ['tee', '-a', file_obj.path],
                        stdin_data=data,
                    )
                else:
                    # Write/truncate mode: write the whole buffer
                    # Use sh -c to redirect stdout to /dev/null so tee's echo doesn't interfere
                    stdout, stderr, rc = await self._kubectl_exec(
                        ['sh', '-c', f'cat > {_shell_quote(file_obj.path)}'],
                        stdin_data=data,
                    )
                
                if rc != 0:
                    self._raise_if_error(stderr, rc, 'close/write')
                
                logger.info(f'Written {len(data)} bytes to {file_obj.path} on pod {file_obj.pod_name}')
            
            # Apply deferred attributes after file is written to pod
            if file_obj.pending_attrs:
                await self._apply_attrs(file_obj.path, file_obj.pending_attrs)
            
            file_obj.closed = True

    async def read(self, file_obj, offset, size):
        """Read data from an open file."""
        if isinstance(file_obj, KubeSFTPFileHandle):
            file_obj.buffer.seek(offset)
            data = file_obj.buffer.read(size)
            if not data:
                raise SFTPError(asyncssh.FX_EOF, '')
            return data
        raise SFTPError(asyncssh.FX_FAILURE, 'Invalid file handle')

    async def write(self, file_obj, offset, data):
        """Write data to an open file's buffer."""
        if isinstance(file_obj, KubeSFTPFileHandle):
            file_obj.buffer.seek(offset)
            file_obj.buffer.write(data)
            return
        raise SFTPError(asyncssh.FX_FAILURE, 'Invalid file handle')

    # ─── File attribute methods ────────────────────────────────────────

    async def stat(self, path):
        """Get file attributes from the user's pod."""
        return await self._get_stat(path, follow_symlinks=True)

    async def lstat(self, path):
        """Get file attributes without following symlinks."""
        return await self._get_stat(path, follow_symlinks=False)

    async def _get_stat(self, path, follow_symlinks=True):
        """Internal stat implementation."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        
        # Use stat command with a custom format to get all needed fields
        # Format: mode permissions nlink uid gid size atime mtime
        stat_flag = '' if follow_symlinks else '-L'
        # Note: In GNU stat, -L follows symlinks (dereference), no flag = don't follow
        # But in the context of lstat we want NOT to follow, and stat we want TO follow
        # GNU stat: -L = dereference (follow symlinks) — this is for stat()
        # Without -L = don't follow — this is for lstat()
        stat_cmd = ['stat']
        if follow_symlinks:
            stat_cmd.append('-L')
        stat_cmd += ['-c', '%f %h %u %g %s %X %Y', path_str]
        
        stdout, stderr, rc = await self._kubectl_exec(stat_cmd)
        if rc != 0:
            self._raise_if_error(stderr, rc, 'stat')
        
        parts = stdout.decode('utf-8').strip().split()
        if len(parts) < 7:
            raise SFTPError(asyncssh.FX_FAILURE, 'Unexpected stat output')
        
        mode = int(parts[0], 16)  # %f gives mode in hex
        nlink = int(parts[1])
        uid = int(parts[2])
        gid = int(parts[3])
        size = int(parts[4])
        atime = int(parts[5])
        mtime = int(parts[6])
        
        return SFTPAttrs(
            size=size,
            uid=uid,
            gid=gid,
            permissions=mode,
            atime=atime,
            mtime=mtime,
        )

    async def setstat(self, path, attrs):
        """Set file attributes on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        await self._apply_attrs(path_str, attrs)

    async def fsetstat(self, file_obj, attrs):
        """Set file attributes on an open file handle (called by scp after upload)."""
        if isinstance(file_obj, KubeSFTPFileHandle):
            if file_obj.is_write and not file_obj.closed:
                # Defer attribute setting until close() when file is actually written to pod
                file_obj.pending_attrs = attrs
            else:
                await self._apply_attrs(file_obj.path, attrs)

    async def _apply_attrs(self, path_str, attrs):
        """Apply file attributes to a path on the user's pod."""
        if attrs.permissions is not None:
            oct_perms = oct(attrs.permissions & 0o7777)
            await self._kubectl_exec(['chmod', oct_perms, path_str])
        
        if attrs.uid is not None and attrs.gid is not None:
            await self._kubectl_exec(['chown', f'{attrs.uid}:{attrs.gid}', path_str])
        
        if attrs.atime is not None and attrs.mtime is not None:
            # Use touch to set modification time
            import datetime
            mtime_str = datetime.datetime.fromtimestamp(attrs.mtime).strftime('%Y%m%d%H%M.%S')
            await self._kubectl_exec(['touch', '-t', mtime_str, path_str])

    # ─── Directory methods ─────────────────────────────────────────────

    async def listdir(self, path):
        """List contents of a directory on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        
        # Use ls -la to get detailed listing, then parse stat info per file
        # Simpler approach: list filenames, then stat each one
        stdout, stderr, rc = await self._kubectl_exec(
            ['ls', '-1a', path_str]
        )
        if rc != 0:
            self._raise_if_error(stderr, rc, 'listdir')
        
        entries = []
        filenames = stdout.decode('utf-8').strip().split('\n')
        
        for filename in filenames:
            filename = filename.strip()
            if not filename:
                continue
            
            full_path = os.path.join(path_str, filename)
            
            try:
                attrs = await self._get_stat(
                    full_path.encode('utf-8') if isinstance(path, bytes) else full_path,
                    follow_symlinks=False
                )
            except SFTPError:
                # If we can't stat a file, create minimal attrs
                attrs = SFTPAttrs()
            
            entry = SFTPName(filename.encode('utf-8'), attrs=attrs)
            self.format_longname(entry)
            entries.append(entry)
        
        return entries

    async def mkdir(self, path, attrs):
        """Create a directory on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        stdout, stderr, rc = await self._kubectl_exec(['mkdir', '-p', path_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'mkdir')

    async def rmdir(self, path):
        """Remove a directory on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        stdout, stderr, rc = await self._kubectl_exec(['rmdir', path_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'rmdir')

    # ─── File management methods ───────────────────────────────────────

    async def remove(self, path):
        """Remove a file on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        stdout, stderr, rc = await self._kubectl_exec(['rm', '-f', path_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'remove')

    async def rename(self, oldpath, newpath):
        """Rename a file or directory on the user's pod."""
        old_str = oldpath.decode('utf-8') if isinstance(oldpath, bytes) else oldpath
        new_str = newpath.decode('utf-8') if isinstance(newpath, bytes) else newpath
        stdout, stderr, rc = await self._kubectl_exec(['mv', old_str, new_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'rename')

    async def posix_rename(self, oldpath, newpath):
        """Rename with POSIX semantics (overwrite if exists)."""
        old_str = oldpath.decode('utf-8') if isinstance(oldpath, bytes) else oldpath
        new_str = newpath.decode('utf-8') if isinstance(newpath, bytes) else newpath
        stdout, stderr, rc = await self._kubectl_exec(['mv', '-f', old_str, new_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'posix_rename')

    # ─── Path methods ──────────────────────────────────────────────────

    async def realpath(self, path):
        """Resolve the real path on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        
        if not path_str or path_str == '.':
            path_str = '/home/dcuuser'
        
        stdout, stderr, rc = await self._kubectl_exec(['realpath', '-m', path_str])
        if rc != 0:
            # If realpath fails, return the path as-is
            if isinstance(path, bytes):
                return path
            return path_str.encode('utf-8')
        
        result = stdout.decode('utf-8').strip()
        return result.encode('utf-8') if isinstance(path, bytes) else result

    async def readlink(self, path):
        """Read the target of a symbolic link on the user's pod."""
        path_str = path.decode('utf-8') if isinstance(path, bytes) else path
        stdout, stderr, rc = await self._kubectl_exec(['readlink', path_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'readlink')
        
        result = stdout.decode('utf-8').strip()
        return result.encode('utf-8') if isinstance(path, bytes) else result

    async def symlink(self, oldpath, newpath):
        """Create a symbolic link on the user's pod."""
        old_str = oldpath.decode('utf-8') if isinstance(oldpath, bytes) else oldpath
        new_str = newpath.decode('utf-8') if isinstance(newpath, bytes) else newpath
        stdout, stderr, rc = await self._kubectl_exec(['ln', '-s', old_str, new_str])
        if rc != 0:
            self._raise_if_error(stderr, rc, 'symlink')


def _shell_quote(s):
    """Shell-escape a string for use in sh -c commands."""
    return "'" + s.replace("'", "'\\''") + "'"
