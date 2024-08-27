from kubernetes import client, config
from traitlets.config import Application
import asyncio
import itertools
from kubessh.pod import UserPod, PodState

class PodManager(Application):
    def __init__(self, namespace="default", process=None, **kwargs):
        super().__init__(**kwargs)
        config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.namespace = namespace
        self.process = process


    def list_pods(self, search_string=None):
        pods_list = self.v1.list_namespaced_pod(self.namespace)
        matching_pods = []

        if search_string is not None:
            matching_pods = [pod for pod in pods_list.items if search_string in pod.metadata.name]
        else:
            matching_pods = pods_list.items

        return matching_pods


    def _print_pods(self, pods):
        output = "-" * 50 + "\r\n"
        if pods:
            output += f"{'NUM':<4} {'NAME':<30} {'STATUS'}\r\n"
            for idx, pod in enumerate(pods, start=1):
                pod_name = pod.metadata.name
                pod_status = pod.status.phase
                if pod.metadata.deletion_timestamp:
                    pod_status = "Terminating"
                output += f"{idx:<4} {pod_name:<30} {pod_status}\r\n"
        else:
            output += "No pods found\r\n"
        output += "=" * 50 + "\r\n"

        if self.process:
            self.process.stdout.write(output.encode('ascii'))
        else:
            print(output)


    async def create_pod(self, username, pod_name=None):
        pod = UserPod(parent=self, username=username, namespace=self.namespace)
        if pod_name:
            pod.pod_name = pod_name

        spinner = itertools.cycle(['-', '/', '|', '\\'])
        
        print("\n", "-" * 50, sep="")

        if self.process:
            self.process.stdout.write(("-" * 50 + "\r\n").encode('ascii'))

            spinner = itertools.cycle(['-', '/', '|', '\\'])

            async for status in pod.ensure_running():
                if status == PodState.RUNNING:
                    self.process.stdout.write('\r\n\033[K'.encode('ascii'))
                    self.process.stdout.write(f"\r'{pod.pod_name}' is already exists.\r\n".encode('ascii'))
                elif status == PodState.STARTING:
                    self.process.stdout.write(f"\r'{pod.pod_name}' creating....".encode('ascii'))
                    self.process.stdout.write('\b'.encode('ascii'))
                    self.process.stdout.write(next(spinner).encode('ascii'))

            self.process.stdout.write(("=" * 50 + "\r\n").encode('ascii'))

        else:
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
            if self.process:
                self.process.stdout.write(f"Error deleting pod or PVC: {e}".encode('ascii'))
            print(f"Error deleting pod or PVC: {e}")

        print("=" * 50)


    async def get_client_input(self, prompt_message):
        self.process.stdout.write(prompt_message.encode('ascii'))
        user_input = b""
        tmp = b""

        while tmp != b'\r':
            tmp = await self.process.stdin.read(1)
            if not tmp:
                break
            user_input += tmp
            self.process.stdout.write(user_input + b'\r')

        return user_input.decode('utf-8').strip()


    async def pod_management_client(self, pod_name):
        username = pod_name.split('-')[1]
        while True:
            user_input = await self.get_client_input("\r\nEnter '1' to list all pods, '2' to connect to pod, '3' to create pod, '4' to delete pod.\r\n")
            matching_pods = self.list_pods(search_string=pod_name)

            if user_input == '1':
                self.process.stdout.write(f"\r\nList of pods user '{username}'\r\n".encode('ascii'))
                self._print_pods(matching_pods)

            elif user_input =='2':
                command = await self.get_client_input("\r\nEnter pod name to connect:\r\n")
                
                new_pod_name = f"{pod_name}-{command}"

                pod = next((pod for pod in matching_pods if pod.metadata.name == new_pod_name), None)

                if not pod:
                    self.process.stdout.write(f"\r\n'{new_pod_name}' was not created.\r\n".encode('ascii'))

                    if len(matching_pods) > 2:
                        self.process.stdout.write(b"\r\nYou already have 3 pods.\r\n")
                        continue

                    user_input = await self.get_client_input(f"\r\nDo you want to create it? [y/n]\r\n")

                    if not user_input.lower() in ('y', 'yes'):
                        self.process.stdout.write(b"\r\nAbort.\r\n")
                        continue

                    await self.create_pod(username, pod_name=new_pod_name)

                if pod.metadata.deletion_timestamp:
                    self.process.stdout.write(f"\r\n'{new_pod_name}' is Terminating.\r\n".encode('ascii'))
                    continue

                self.process.stdout.write("\r\n\n".encode('ascii'))

                return new_pod_name

            elif user_input == '3':
                if len(matching_pods) > 2:
                    self.process.stdout.write(b"\r\nYou already have 3 pods.\r\n")
                    continue

                command = await self.get_client_input("\r\nEnter pod name to create:\r\n")

                new_pod_name = f"{pod_name}-{command}"

                await self.create_pod(username, pod_name=new_pod_name)

            elif user_input =='4':
                if len(matching_pods) < 1:
                    self.process.stdout.write(b"\r\nNo pods to delete.\r\n\n")
                    continue

                command = await self.get_client_input("\r\nEnter pod name to delete:\r\n")

                new_pod_name = f"{pod_name}-{command}"
                
                if not any(pod.metadata.name == new_pod_name for pod in matching_pods):
                    self.process.stdout.write(f"\r\n'{new_pod_name}' not found.\r\n".encode('ascii'))
                    continue

                user_input = await self.get_client_input(f"\r\nAre you sure to delete '{new_pod_name}'? [Y/n]\r\n")

                if not user_input.lower() in ('y', 'yes'):
                    self.process.stdout.write(b"\r\nAbort.\r\n")
                    continue

                await self.delete_pod(new_pod_name)
                self.process.stdout.write(f"\r\n'{pod_name}' is deleted.\r\n".encode('ascii'))


    async def pod_management_developer(self):
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

