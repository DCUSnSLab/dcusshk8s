import asyncio
import asyncssh
import subprocess
from ptyprocess import PtyProcess
import time
import argparse
import os
import sys
import uuid
from kubernetes import client as k
import kubernetes.config
import escapism
import functools
from enum import Enum
import shlex
import string
from concurrent.futures import ThreadPoolExecutor
from traitlets.config import LoggingConfigurable
from traitlets import Dict, Unicode, List, default

from .serialization import make_api_object_from_dict

try:
    kubernetes.config.load_incluster_config()
except kubernetes.config.ConfigException:
    kubernetes.config.load_kube_config()

# FIXME: Figure out if making this global is a problem
v1 = k.CoreV1Api()

class PodState(Enum):
    UNKNOWN = 0
    STARTING = 1
    RUNNING = 2

class UserPod(LoggingConfigurable):
    """
    A kubernetes pod of specific configuration for one user.

    There might be multiple shells opened concurrently to this pod.

    Config from administrators and the ssh command from the user are
    mapped here to a running Kubernetes pod. This allows multiple ssh
    sessions to be running concurrently in the same kubernetes pod.

    Config from administrators is set via traitlets in config.
    """
    pod_template = Dict(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {},
            "spec": {
                "automountServiceAccountToken": False,
                "nodeSelector": { 
                    "kubessh": "general_node"
                },
                "initContainers": [
                    {
                        "name": "init-setup",
                        "image": "harbor.cu.ac.kr/swlabpods/dbuntu:latest",
                        "command": ["/bin/bash","-c"],
                        "args": [
                            """
                            mkdir -p /mnt/usr /mnt/lib /mnt/etc /mnt/var/lib/dpkg /mnt/var/lib/apt /mnt/var/cache/apt /mnt/home;
                            if [ ! -f /mnt/usr/bin/bash ]; then
                             cp -a /usr/* /mnt/usr/;
                             cp -a /lib/* /mnt/lib/;
                             cp -a /etc/* /mnt/etc/;
                             cp -a /var/* /mnt/var/;
                             chmod 4755 /mnt/usr/bin/sudo;
                            fi;
                            chmod 755 /mnt /mnt/home /mnt/usr /mnt/lib /mnt/etc /mnt/var
                            """
                        ],
                        "env": [
                            {
                                "name": "PATH",
                                "value": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                            }
                        ],
                        "securityContext": {
                            "runAsUser": 0
                        },
                        "volumeMounts": [
                            {
                                "name": "poddata",
                                "mountPath": "/mnt/usr",
                                "subPath": "usr"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/mnt/lib",
                                "subPath": "lib"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/mnt/etc",
                                "subPath": "etc"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/mnt/var",
                                "subPath": "var"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/mnt/home",
                                "subPath": "home"
                            }
                        ]
                    }
                ],
                "containers": [
                    {
                        "command": ["/bin/bash", "-c"],
                        "args": [
                            """
                            sudo chown dcuuser:dcuuser /home/dcuuser;
                            cp -n /etc/skel/.* /home/dcuuser/;
                            if [ ! -f /home/dcuuser/.vimrc ]; then
                                echo -e 'if has ("syntax")\\n    syntax on\\nendif\\n\\nset autoindent\\nset cindent\\nset nu\\n\\nset smartindent\\nset tabstop=4\\nset shiftwidth=4' > /home/dcuuser/.vimrc;
                            fi;
                            while true; do sleep 10; done
                            """
                        ],
                        "env": [
                           {
                              "name": "PATH",
                              "value": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                           }
                        ],
                        "image": "harbor.cu.ac.kr/swlabpods/dbuntu:latest",
                        "name": "shell",
                        "stdin": True,
                        "tty": True,
                        "resources": {
                           "requests": {
                                   "cpu": "50m",
                                   "memory": "150Mi",
                           },
                           "limits": {
                                       "cpu": "100m",
                                       "memory": "200Mi",
                           },
                        },
                        "volumeMounts": [
                            {
                                "name": "poddata",
                                "mountPath": "/usr",
                                "subPath": "usr"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/lib",
                                "subPath": "lib"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/etc",
                                "subPath": "etc"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/var",
                                "subPath": "var"
                            },
                            {
                                "name": "poddata",
                                "mountPath": "/home",
                                "subPath": "home"
                            }
                        ]
                    }
                ],
                "volumes": [
                    {
                        "name": "poddata",
                    }
                ],
            },
        },
        help="""
        Template for creating user pods.

        This should be a dict containing a fully specified Kubernetes
        Pod object. Specific components of it may be changed to
        match the configuration of the Shell object requested.
        """,
        config=True
    )

    pvc_templates = List(
        [
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": "test3-pvc",
                },
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "volumeMode": "Filesystem",
                    "resources": {
                            "requests": {
                                    "storage": "5Gi",
                            },
                    },
                    "storageClassName": "normal-r3",
                    #"storageClassName": "openebs-hostpath",
                },
            },
            
        ],
        help="""
            List of templates for creating user persistent volume claims.
            Elements should be dicts with fully specified Kubernetes
            PersistentVolumeClaim objects. If empty (the default), no persistent
            volumes will be created. The templates must ensure that claim names are
            unique by including the string '{username}', which is expanded to the
            name of the user that the shell belongs to. In order to use the created
            persistent volumes, they should be referenced in the pod_template's
            spec.volumes.
            """,
        config=True
    )

    username = Unicode(
        None,
        allow_none=True,
        help="""
        Username this shell belongs to.

        Will be sanitized wherever required.
        """,
        config=True
    )

    pod_name = Unicode(
        None,
        allow_none=True,
        help="""
        Name of this particular pod.

        Auto-generated to be 'ssh-{username}' if not set.
        """,
    )

    @default('pod_name')
    def _pod_name_default(self):
        return self._expand_all("ssh-{username}")

    namespace = Unicode(
        None,
        allow_none=True,
        help="""
        Kubernetes Namespace this shell will be spawned into.

        This namespace must already exist.
        """,
    )


    def _expand_user_properties(self, template):
        # Make sure username and servername match the restrictions for DNS labels
        # Note: '-' is not in safe_chars, as it is being used as escape character
        safe_chars = set(string.ascii_lowercase + string.digits)

        safe_username = escapism.escape(self.username, safe=safe_chars, escape_char='-').lower()

        return template.format(
            username=safe_username,
        )

    def _expand_all(self, src):
        if isinstance(src, list):
            return [self._expand_all(i) for i in src]
        elif isinstance(src, dict):
            return {k: self._expand_all(v) for k, v in src.items()}
        elif isinstance(src, str):
            return self._expand_user_properties(src)
        else:
            return src

    def __init__(self, username, namespace, *args, **kwargs):
        self.username = username
        self.namespace = namespace
        super().__init__(*args, **kwargs)

        self.required_labels = {
            'kubessh.yuvi.in/username': escapism.escape(self.username, escape_char='-'),
            'kubessh': 'userpods'
        }

        # Threads required to perform all activities in this shell
        # These should probably be a well sized global threadpool, since this is being
        # used as a sort of queue. We will currently use a threadpool of 1 thread per shell
        # for simplicity. The number of threads here needs to be the maximum number of threads
        # this object could possibly use at the same time. Eventually, this needs to be a
        # global threadpool with well enforced limits #FIXME
        self.kube_api_threadpool = ThreadPoolExecutor(1)

    def _run_in_executor(self, func, *args, **kwargs):
        return asyncio.get_event_loop().run_in_executor(self.kube_api_threadpool, functools.partial(func, *args, **kwargs))

    def _make_labelselector(self, labels):
        return ','.join([f'{k}={v}' for k, v in labels.items()])

    def make_pod_spec(self):
        pod = make_api_object_from_dict(self._expand_all(self.pod_template), k.V1Pod)
        pod.metadata.name = self.pod_name
        # print(pod)
        pod.spec.volumes[0].persistent_volume_claim = k.V1PersistentVolumeClaimVolumeSource(claim_name = self.pod_name + '-pvc')
        if pod.metadata.labels is None:
            pod.metadata.labels = {}
        pod.metadata.labels.update(self.required_labels)

        return pod

    def make_pvc_spec(self, template):
        # print('test pvc')
        # print(template)
        pvc = make_api_object_from_dict(self._expand_all(template), k.V1PersistentVolumeClaim)
        pvc.metadata.name = self.pod_name + '-pvc'
        # print(pvc)
        if pvc.metadata.labels is None:
            pvc.metadata.labels = {}
        pvc.metadata.labels.update(self.required_labels)

        return pvc

    async def ensure_running(self):
        """
        Ensure this user pod is running.

        1. If pod already exists, and is in running state, just return
        2. If pod already exists, and has completed, delete it.
        3. If pod doesn't exist, create new pod & wait for it to be running
        """
        try:
            pod = await self._run_in_executor(
                v1.read_namespaced_pod,
                self.pod_name, self.namespace
            )
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                pod = None
            else:
                raise

        if pod and pod.status.phase == 'Running':
            # Pod exists, and is running. Nothing to do
            self.pod = pod
            yield PodState.RUNNING
            return

        # FIXME: Deal with pods in Terminating state
        if pod and pod.status.phase in ['Failed', 'Succeeded']:
            # Pod exists, but is in an unusable state.
            # Delete it, and say there is no pod
            await self._run_in_executor(
                v1.delete_namespaced_pod,
                pod.metadata.name,
                pod.metadata.namespace, body=k.V1DeleteOptions(grace_period_seconds=0)
            )
            pod = None
        # print(self.pod_template)
        if not pod:
            # There is no pod, so start one!
            yield PodState.STARTING

            # create persistent volumes, if any
            for template in self.pvc_templates:
                pvc_spec = self.make_pvc_spec(template)
                try:
                    pvc = await self._run_in_executor(v1.create_namespaced_persistent_volume_claim, self.namespace, pvc_spec)
                    self.log.info(f"Successfully created PVC {pvc.metadata.name}")
                    self.log.debug(pvc)
                except kubernetes.client.rest.ApiException as e:
                    if e.status == 409:
                        self.log.info(f"PVC {pvc_spec.metadata.name} already exists, did not create a new PVC.")
                    elif e.status == 403:
                        t, v, tb = sys.exc_info()
                        try:
                            pvc = await self._run_in_executor(v1.read_namespaced_persistent_volume_claim, pvc_spec.metadata.name, self.namespace, pvc_spec)
                        except:
                            raise v.with_traceback(tb)
                        self.log.info(f"PVC {pvc_spec.metadata.name} already exists, possibly have reached quota.")
                    else:
                        raise

            pod = await self._run_in_executor(
                v1.create_namespaced_pod,
                self.namespace, self.make_pod_spec()
            )

        while pod.status.phase != 'Running':
            # By now, a pod exists but is not necessarily in 'Running' state
            # So we just wait for that to be the case, and return
            yield PodState.STARTING
            await asyncio.sleep(1)
            pod = await self._run_in_executor(
                v1.read_namespaced_pod,
                pod.metadata.name, pod.metadata.namespace
            )
        yield PodState.RUNNING

    def _cleanup_background_processes(self, session_id):
        """
        SSH 세션 종료 시 KUBESSH_SESSION_ID가 일치하는 단발성 및 백그라운드(nohup, &) 
        프로세스를 모두 찾아내서 강제 종료(kill -9) 합니다.
        """
        kill_cmd = [
            'kubectl', '--namespace', self.namespace,
            'exec', '-c', 'shell', self.pod_name, '--',
            'sh', '-c',
            'for p in $(ls /proc 2>/dev/null | grep "^[0-9]"); do '
            f'grep -qz "KUBESSH_SESSION_ID={session_id}" /proc/$p/environ 2>/dev/null && '
            'kill -9 $p 2>/dev/null; done'
        ]
        subprocess.run(kill_cmd, timeout=5, capture_output=True)

    async def execute(self, ssh_process):
        """
        SSH 세션에서 명령을 실행합니다.

        TTY 모드와 Non-TTY 모드로 분기되며, 각 모드에서 동일한 종료 로직을 따름
        - TTY 모드  : PtyProcess를 사용. 제어 신호를 PTY에 직접 사용
        - Non-TTY 모드: asyncio subprocess + PIPE를 사용. 제어 신호는 OS 시그널로 보냄

        공통 종료 순서 (Stage 1 → 2 → 3 → 4):
          1. EOF/stdin close   - 프로세스에 정상 종료 기회 (2초 대기)
          2. SIGTERM           - 정상 종료 재시도 (5초 대기)
          3. SIGKILL           - 강제 종료
          4. 환경변수 기반 잔여 프로세스 일괄 정리 (_cleanup_background_processes)
        """
        session_id = uuid.uuid4().hex[:12]
        command = shlex.split(ssh_process.command) if ssh_process.command else ["/bin/bash", "-l"]
        tty_args = ['--tty'] if ssh_process.get_terminal_type() else []
        kubectl_command = [
            'kubectl',
            '--namespace', self.namespace,
            'exec',
            '-c', 'shell',
            '--stdin'
            ] + tty_args + [
            self.pod_name,
            '--',
            'env', f'KUBESSH_SESSION_ID={session_id}'
        ] + command

        # =====================================================================
        # [ TTY 모드 ] 대화형 가상 터미널 (ex: ssh user@ip)
        #   - PtyProcess로 kubectl exec를 실행하고 asyncssh와 입출력을 연결합니다.
        #   - TerminalSizeChanged 이벤트를 PTY에 실시간으로 전달합니다.
        # =====================================================================
        if ssh_process.get_terminal_type():
            # PtyProcess and asyncssh disagree on ordering of terminal size
            ts = ssh_process.get_terminal_size()
            process = PtyProcess.spawn(argv=kubectl_command, dimensions=(ts[1], ts[0]))
            await ssh_process.redirect(process, process)

            loop = asyncio.get_event_loop()

            # Future for spawned process dying
            # We explicitly create a threadpool of 1 threads for every run_in_executor call
            # to help reason about interaction between asyncio and threads. A global threadpool
            # is fine when using it as a queue (when doing HTTP requests, for example), but not
            # here since we could end up deadlocking easily.
            executor = ThreadPoolExecutor(1)
            shell_completed = loop.run_in_executor(executor, process.wait)
            # Future for ssh connection closing
            read_stdin = asyncio.ensure_future(ssh_process.stdin.read())

            is_connection_lost = False

            # This loops is here to pass TerminalSizeChanged events through to ptyprocess
            # It needs to break when the ssh connection is gone or when the spawned process is gone.
            # See https://github.com/ronf/asyncssh/issues/134 for info on how this works
            while not ssh_process.stdin.at_eof() and not shell_completed.done():
                try:
                    if read_stdin.done():
                        read_stdin = asyncio.ensure_future(ssh_process.stdin.read())
                    done, _ = await asyncio.wait([read_stdin, shell_completed], return_when=asyncio.FIRST_COMPLETED)
                    # asyncio.wait doesn't await the futures - it only waits for them to complete.
                    # We need to explicitly await them to retreive any exceptions from them
                    for future in done:
                        await future
                except asyncssh.misc.TerminalSizeChanged as exc:
                    process.setwinsize(exc.height, exc.width)
                except Exception as exc:
                    # at_eof() 설정 전에 ConnectionLost 등의 예외가 먼저 발생할 수 있음
                    self.log.warning(f"SSH connection lost unexpectedly: {exc}")
                    is_connection_lost = True
                    break

            self.log.warning(f'[DEBUG] Loop exited: shell_done={shell_completed.done()}, lost={is_connection_lost}, session={session_id}')

            # SSH가 끊겼는데 프로세스가 아직 살아있는 경우에만 종료 시퀀스를 실행
            if (ssh_process.stdin.at_eof() or is_connection_lost) and not shell_completed.done():
                # Stage 1: EOF (Ctrl+D) → PTY 전송
                try:
                    process.write(b'\x04')
                    self.log.info('TTY shutdown stage 1: sent EOF to PTY')
                except Exception:
                    self.log.warning('TTY shutdown stage 1: failed to send EOF')
                try:
                    await asyncio.wait_for(asyncio.shield(shell_completed), timeout=2)
                    self.log.info('TTY process exited after EOF (stage 1)')
                except asyncio.TimeoutError:
                    pass

                if not shell_completed.done():
                    # Stage 2: SIGTERM
                    try:
                        await loop.run_in_executor(executor, lambda: process.terminate(force=False))
                        self.log.info('TTY shutdown stage 2: sent SIGTERM')
                    except Exception:
                        self.log.warning('TTY shutdown stage 2: failed to send SIGTERM')
                    try:
                        await asyncio.wait_for(asyncio.shield(shell_completed), timeout=5)
                        self.log.info('TTY process exited after SIGTERM (stage 2)')
                    except asyncio.TimeoutError:
                        pass

                if not shell_completed.done():
                    # Stage 3: SIGKILL
                    try:
                        await loop.run_in_executor(executor, lambda: process.terminate(force=True))
                        self.log.info('TTY shutdown stage 3: sent SIGKILL')
                    except Exception:
                        self.log.warning('TTY shutdown stage 3: failed to send SIGKILL')

            # Stage 4: 환경변수(KUBESSH_SESSION_ID)로 잔여 백그라운드 프로세스 일괄 정리
            try:
                await loop.run_in_executor(executor, self._cleanup_background_processes, session_id)
                self.log.info(f'TTY cleanup completed for session: {session_id}')
            except Exception as e:
                self.log.warning(f'TTY cleanup failed: {e}')
            finally:
                executor.shutdown(wait=False)

            ssh_process.exit(shell_completed.result() if shell_completed.done() else 255)

        # =====================================================================
        # [ Non-TTY 모드 ] 1회성 명령 실행 (ex: ssh user@ip "sleep 100")
        #   - Non-TTY 모드는 구현되지 않음. 추후 구현 가능성을 위해 종료 시퀀스 구현함
        #   - asyncio subprocess + PIPE로 kubectl exec를 실행합니다.
        #   - 제어 신호는 OS 시그널(SIGTERM/SIGKILL)로 보냅니다.
        # =====================================================================
        else:
            process = await asyncio.create_subprocess_exec(
                *kubectl_command,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await ssh_process.redirect(stdin=process.stdin, stdout=process.stdout, stderr=process.stderr)

            process_wait_task = asyncio.ensure_future(process.wait())

            async def _watch_ssh_disconnect():
                """SSH stdin EOF 감지 → SSH 연결 끊김 판단용"""
                try:
                    while not ssh_process.stdin.at_eof():
                        await ssh_process.stdin.read()
                except Exception:
                    pass

            ssh_watch_task = asyncio.ensure_future(_watch_ssh_disconnect())

            # 프로세스 종료 또는 SSH 연결 끊김 중 먼저 발생하는 이벤트를 기다림
            done, pending = await asyncio.wait(
                [process_wait_task, ssh_watch_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            if process_wait_task.done():
                # 정상 종료: 명령어가 먼저 끝난 경우
                ssh_watch_task.cancel()
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self._cleanup_background_processes, session_id)
                    self.log.info(f'Non-TTY cleanup completed (normal exit) for session: {session_id}')
                except Exception as e:
                    self.log.warning(f'Non-TTY cleanup failed: {e}')
                ssh_process.exit(process_wait_task.result())

            else:
                # 비정상 종료: SSH 연결이 먼저 끊긴 경우
                self.log.info('Non-TTY: SSH client disconnected, starting graceful shutdown')

                # Stage 1: stdin pipe 닫기 (EOF 전송)
                try:
                    if process.stdin and not process.stdin.is_closing():
                        process.stdin.close()
                    self.log.info('Non-TTY shutdown stage 1: closed stdin pipe')
                except Exception:
                    self.log.warning('Non-TTY shutdown stage 1: failed to close stdin')
                try:
                    await asyncio.wait_for(asyncio.shield(process_wait_task), timeout=2)
                    self.log.info('Non-TTY process exited after stdin close (stage 1)')
                except asyncio.TimeoutError:
                    pass

                if not process_wait_task.done():
                    # Stage 2: SIGTERM
                    try:
                        process.terminate()
                        self.log.info('Non-TTY shutdown stage 2: sent SIGTERM')
                    except Exception:
                        self.log.warning('Non-TTY shutdown stage 2: failed to send SIGTERM')
                    try:
                        await asyncio.wait_for(asyncio.shield(process_wait_task), timeout=5)
                        self.log.info('Non-TTY process exited after SIGTERM (stage 2)')
                    except asyncio.TimeoutError:
                        pass

                if not process_wait_task.done():
                    # Stage 3: SIGKILL
                    try:
                        process.kill()
                        self.log.info('Non-TTY shutdown stage 3: sent SIGKILL')
                    except Exception:
                        self.log.warning('Non-TTY shutdown stage 3: failed to send SIGKILL')

                # Stage 4: 환경변수(KUBESSH_SESSION_ID)로 잔여 백그라운드 프로세스 일괄 정리
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self._cleanup_background_processes, session_id)
                    self.log.info(f'Non-TTY cleanup completed for session: {session_id}')
                except Exception as e:
                    self.log.warning(f'Non-TTY cleanup failed: {e}')

                ssh_process.exit(process_wait_task.result() if process_wait_task.done() else 255)
