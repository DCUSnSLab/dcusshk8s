from kubernetes import client, config
from traitlets.config import Application
import asyncio
from kubessh.pod import UserPod, PodState

class PodManager(Application):
    def __init__(self, namespace="default"):
        config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.namespace = namespace

    def list_pods(self, search_string=None):
        pods_list = self.v1.list_namespaced_pod(self.namespace)
        matching_pods = []

        if search_string is not None:
            matching_pods = [pod for pod in pods_list.items if search_string in pod.metadata.name]
        else:
            matching_pods = pods_list.items

        return matching_pods

    async def create_pod(self, pod_name):
        print("\n", "-" * 50, sep="")
        pod = UserPod(parent=self, username=pod_name, namespace=self.namespace)
        async for status in pod.ensure_running():
            pass
        print("=" * 50)

    async def delete_pod(self, pod_name):
        try:
            print("\n", "-" * 50, sep="")
            print(f"Deleting pod '{pod_name}'...")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.v1.delete_namespaced_pod(name=pod_name, namespace=self.namespace)
            )
            print(f"Pod '{pod_name}' deleted.")

            pvc_name = f"{pod_name}-pvc"

            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.v1.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=self.namespace)
            )
            print(f"PVC '{pvc_name}' deleted.")

        except Exception as e:
            print(f"Error deleting pod or PVC: {e}")

        print("=" * 50)

    async def select_and_connect_pod(self, process, pod_name):
        username = pod_name.split('-')[1]
        while True:
            matching_pods = self.list_pods(search_string=pod_name)
            
            process.stdout.write(f"\r\nlist of pods user '{username}'\r\n".encode('ascii'))
            self._print_pods(matching_pods, process=process)
            
            process.stdout.write(b"\r\nEnter the name of the pod to connect or create a new one: \r\n")

            command = b""
            while not command.endswith(b'\r'):
                tmp = await process.stdin.read(1)
                if not tmp:
                    break
                command += tmp
                process.stdout.write(f"{command.decode('utf-8')}\r".encode('ascii'))


            process.stdout.write(b"\r\n\n")
            command = command.decode('utf-8').strip()
            
            new_pod_name = f"{pod_name}-{command}"

            return new_pod_name


    async def interactive_input(self):
        while True:
            print("\nEnter '1' to list all pods, '2' to search for pods, '3' to create pod, '4' to delete pod.")
            user_input = await asyncio.get_event_loop().run_in_executor(None, input)
            user_input = user_input.strip()

            if user_input == '1':
                pods = self.list_pods()
                self._print_pods(pods)
            elif user_input == '2':
                print("Enter search username >> ", end="")
                search_string = await asyncio.get_event_loop().run_in_executor(None, input)
                pods = self.list_pods(search_string)
                self._print_pods(pods)
            elif user_input == '3':
                print("Enter pod name >> ", end="")
                pod_name = await asyncio.get_event_loop().run_in_executor(None, input)
                await self.create_pod(pod_name)
            elif user_input == '4':
                print("Enter pod name to delete >> ", end="")
                pod_name = await asyncio.get_event_loop().run_in_executor(None, input)
                await self.delete_pod(pod_name)


    def _print_pods(self, pods, process=None):
        output = ""
        if pods:
            output += "-" * 50 + "\r\n"
            output += f"{'NUM':<4} {'NAME':<30} {'STATUS'}\r\n"
            for idx, pod in enumerate(pods, start=1):
                pod_name = pod.metadata.name
                pod_status = pod.status.phase
                if pod.metadata.deletion_timestamp is not None: 
                    pod_status = "Terminating"
                output += f"{idx:<4} {pod_name:<30} {pod_status}\r\n"
        else:
            output += "No pods found\r\n"
        output += "=" * 50 + "\r\n"
        
        if process:
            process.stdout.write(output.encode('ascii'))
        else:
            print(output)

    '''
    def _print_pods(self, pods):
        if pods:
            print("-" * 50)
            print(f"{'NUM':<4} {'NAME':<30} {'STATUS'}")
            for idx, pod in enumerate(pods, start=1):
                pod_name = pod.metadata.name
                pod_status = pod.status.phase
                if pod.metadata.deletion_timestamp is not None: pod_status = "Terminating"
                print(f"{idx:<4} {pod_name:<30} {pod_status}")
        else:
            print("No pods found")
        print("=" * 50)
    '''
