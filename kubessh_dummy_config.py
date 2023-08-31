from kubessh.authentication.dummy import DummyAuthenticator

# Make sure this exists
c.KubeSSH.host_key_path = './kubessh/dummy-kubessh-host-key'

c.KubeSSH.debug = False
c.KubeSSH.authenticator_class = DummyAuthenticator
c.KubeSSH.default_namespace = 'swlabpods'
