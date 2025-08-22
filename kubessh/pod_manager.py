from kubernetes import client, config
import asyncio
import itertools
from kubessh.pod import UserPod, PodState

MAX_PODS_PER_USER = 5


class PodManager:
    def __init__(self, namespace="default"):
        config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.namespace = namespace

    def list_pods(self, search_string=None):
        pods_list = self.v1.list_namespaced_pod(self.namespace)
        if search_string:
            matching_pods = [pod for pod in pods_list.items if search_string in pod.metadata.name]
        else:
            matching_pods = pods_list.items
        return matching_pods

    async def create_pod(self, username, pod_name=None, image=None, cpu=None, mem=None, gpu=False):
        pod = UserPod(username=username, namespace=self.namespace)
        if pod_name:
            pod.pod_name = pod_name
        if image == 'Rocky:9':
            pod.pod_template["spec"]["containers"][0]["image"] = "harbor.cu.ac.kr/swlabpods_test/rocky:latest"
        elif image == 'Debian:12':
            pod.pod_template["spec"]["containers"][0]["image"] = "harbor.cu.ac.kr/swlabpods_test/debian:latest"

        if cpu:
            print(cpu)
            pod.pod_template["spec"]["containers"][0]["resources"]["limits"]["cpu"] = f"{cpu}"
        if mem:
            print(mem)
            pod.pod_template["spec"]["containers"][0]["resources"]["limits"]["memory"] = f"{mem}"
        if gpu:
            pod.pod_template["spec"]["nodeSelector"] = {"gpu": "true"}
            pod.pod_template["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] = "1"

        async for status in pod.ensure_running():
            pass

    async def delete_pod(self, pod_name):
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self.v1.delete_namespaced_pod(name=pod_name, namespace=self.namespace)
        )
        pvc_name = f"{pod_name}-pvc"
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self.v1.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=self.namespace)
        )


class ClientPodManager(PodManager):
    def __init__(self, process, pod_name, namespace="default"):
        super().__init__(namespace)
        self.process = process
        self.pod_name = pod_name

    async def get_client_input(self, prompt_message):
        self.process.stdout.write(prompt_message.encode('utf-8'))
        user_input = b""
        while True:
            tmp = await self.process.stdin.read(1)
            if not tmp or tmp == b'\r':
                break
            elif tmp == b'\x08' and user_input:
                user_input = user_input[:-1]
                self.process.stdout.write(b'\b \b')
            elif not tmp == b'\x08':
                user_input += tmp
                self.process.stdout.write(tmp)
        return user_input.decode('utf-8').strip()

    async def get_validated_input(self, min_value, max_value, unit='', value_name='value'):
        while True:
            user_input = await self.get_client_input(f"\r\n\r\nEnter {value_name} allocation. Between {min_value} ~ {max_value} {unit}\r\n'd' - default   \
                                                        \r\n'q' - cancel    \
                                                        \r\nEnter the value >> ")
            #user_input = await self.get_client_input(prompt)
            if user_input.lower() == 'q':
                self.process.stdout.write("\r\nOperation aborted.\r\n".encode('utf-8'))
                return 'q'
            if user_input.lower() == 'd':
                return None
            try:
                if user_input.endswith(unit):
                    user_input = user_input.rstrip(unit)
                input_value = int(user_input)
                if min_value <= input_value <= max_value:
                    return f"{input_value}{unit}"
                else:
                    self.process.stdout.write(f"\r\nInvalid {value_name} value. Must be between {min_value} {unit} and {max_value} {unit}".encode('utf-8'))
            except ValueError:
                self.process.stdout.write(f"\r\nInvalid {value_name} value. Must be between {min_value} {unit} and {max_value} {unit}".encode('utf-8'))

    def _print_pods(self, pods):
        output = "\r\n" + "-" * 50 + "\r\n"
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
        self.process.stdout.write(output.encode('utf-8'))

    async def pod_management(self):
        username = self.pod_name.split('-')[1]

        while True:
            user_input = await self.get_client_input("\r\n[Pod Management]  \
                                                    \r\n1 - List all pods   \
                                                    \r\n2 - Connect to pod  \
                                                    \r\n3 - Create pod      \
                                                    \r\n4 - Delete pod      \
                                                    \r\nq - exit            \
                                                    \r\nSelect an option >> "
            )
            matching_pods = self.list_pods(search_string=self.pod_name)

            if user_input == '1':
                self.process.stdout.write(f"\r\n\r\nList of pods for user '{username}'".encode('utf-8'))
                self._print_pods(matching_pods)

            elif user_input == '2':
                identifier = await self.get_client_input("\r\n\r\nEnter pod identifier to connect >> ")
                new_pod_name = f"{self.pod_name}-{identifier}"
                pod = next((pod for pod in matching_pods if pod.metadata.name == new_pod_name), None)
                if not pod:
                    self.process.stdout.write(f"\r\n\r\nPod '{new_pod_name}' does not exist.\r\n".encode('utf-8'))
                    continue
                elif pod.metadata.deletion_timestamp:
                    self.process.stdout.write(f"\r\nPod '{new_pod_name}' is terminating.\r\n".encode('utf-8'))
                    continue
                self.process.stdout.write("\r\n\r\n\r\n".encode('utf-8'))
                return new_pod_name

            elif user_input == '3':
                if len(matching_pods) >= MAX_PODS_PER_USER:
                    self.process.stdout.write(f"\r\n\r\nYou already have {MAX_PODS_PER_USER} pods.\r\n".encode('utf-8'))
                    continue

                identifier = await self.get_client_input("\r\n\r\nEnter pod identifier to create >> ")
                new_pod_name = f"{self.pod_name}-{identifier}"

                pod = next((pod for pod in matching_pods if pod.metadata.name == new_pod_name), None)
                if pod:
                    self.process.stdout.write(f"\r\n\r\nPod '{new_pod_name}' already exist.\r\n".encode('utf-8'))
                    continue

                custom = await self.get_client_input(f"\r\n\r\nDo you want to customize resources? [y/n] >> ")
                if custom.lower() in ('y', 'yes'):
                    image = await self.get_client_input("\r\n\r\nSelect the base image for your container\
                                                        \r\n1 - Ubuntu 20.04 (default)\
                                                        \r\n2 - Rocky 9\
                                                        \r\n3 - Debian 12\
                                                        \r\nSelect an option >> ")
                    if image == '1':
                        image = "Ubuntu 20.04"
                    elif image == '2':
                        image = "Rocky 9"
                    elif image == '2':
                        image = "Debian 12"
                    else:
                        image = None
                    cpu = await self.get_validated_input(50, 200, unit='m', value_name='CPU')
                    if cpu == 'q':
                        continue
                    mem = await self.get_validated_input(150, 300, unit='Mi', value_name='Memory')
                    if mem == 'q':
                        continue
                    gpu = await self.get_client_input("\r\n\r\nDo you want to allocate GPU? [y/n] >> ")
                    if gpu.lower() in ('y', 'yes'):
                        gpu = True
                    else:
                        gpu = False
                else:
                    image = None
                    cpu = None
                    mem = None
                    gpu = False

                resource_info = (
                    f"Selected Resources:\r\n"
                    f"  - Image : {image if image else 'Ubuntu 20.04 (default)'}\r\n"
                    f"  - CPU : {cpu if cpu else '100m (default)'}\r\n"
                    f"  - Memory : {mem if mem else '200Mi (default)'}\r\n"
                    f"  - GPU : {'Yes' if gpu else 'No'}"
                )
                confirm = await self.get_client_input(f"\r\n\n{resource_info}\r\nDo you want to create pod '{new_pod_name}'? [y/n] >> ")
                if confirm.lower() in ('y', 'yes'):
                    await self.create_pod(username, pod_name=new_pod_name, image=image, cpu=cpu, mem=mem, gpu=gpu)
                    self.process.stdout.write(f"\r\nPod '{new_pod_name}' created.\r\n".encode('utf-8'))
                else:
                    self.process.stdout.write(b"\r\nOperation aborted.\r\n")

            elif user_input == '4':
                if not matching_pods:
                    self.process.stdout.write(b"\r\n\r\nNo pods to delete.\r\n")
                    continue
                identifier = await self.get_client_input("\r\n\r\nEnter pod identifier to delete >> ")
                new_pod_name = f"{self.pod_name}-{identifier}"
                if not any(pod.metadata.name == new_pod_name for pod in matching_pods):
                    self.process.stdout.write(f"\r\n\r\nPod '{new_pod_name}' not found.\r\n".encode('utf-8'))
                    continue
                confirm = await self.get_client_input(f"\r\n\r\nAre you sure you want to delete pod '{new_pod_name}'? [y/n] >> ")
                if confirm.lower() in ('y', 'yes'):
                    await self.delete_pod(new_pod_name)
                    self.process.stdout.write(f"\r\n\r\nPod '{new_pod_name}' deleted.\r\n".encode('utf-8'))
                else:
                    self.process.stdout.write(b"\r\n\r\nOperation aborted.\r\n")

            elif user_input.lower() in ('q', 'exit'):
                self.process.stdout.write(b"\r\n\r\nExiting pod management.\r\n")
                self.process.exit(0)
                break

            else:
                self.process.stdout.write(b"\r\nInvalid option. Please try again.\r\n")


class AdminPodManager(PodManager):
    def __init__(self, namespace="default"):
        super().__init__(namespace)

    def _print_pods(self, pods):
        print("\n" + "-" * 50)
        if pods:
            print(f"{'NUM':<4} {'NAME':<30} {'STATUS'}")
            for idx, pod in enumerate(pods, start=1):
                pod_name = pod.metadata.name
                pod_status = pod.status.phase
                if pod.metadata.deletion_timestamp:
                    pod_status = "Terminating"
                print(f"{idx:<4} {pod_name:<30} {pod_status}")
        else:
            print("No pods found")
        print("=" * 50)

    async def pod_management(self):
        while True:
            print("\n[Admin Pod Management]")
            print("1 - List all pods")
            print("2 - Search for pods")
            print("3 - Create pod")
            print("4 - Delete pod")
            user_input = await asyncio.get_event_loop().run_in_executor(None, input, "Select an option >> ")
            user_input = user_input.strip()
            if user_input == '1':
                pods = self.list_pods()
                self._print_pods(pods)

            elif user_input == '2':
                search_string = await asyncio.get_event_loop().run_in_executor(None, input, "Enter search string >> ")
                pods = self.list_pods(search_string)
                self._print_pods(pods)

            elif user_input == '3':
                pod_name = await asyncio.get_event_loop().run_in_executor(None, input, "Enter pod name to create >> ")
                username = pod_name.split('-')[1]
                await self.create_pod(username, pod_name=pod_name)
                print(f"Pod '{pod_name}' created.")

            elif user_input == '4':
                pod_name = await asyncio.get_event_loop().run_in_executor(None, input, "Enter pod name to delete >> ")
                confirm = await asyncio.get_event_loop().run_in_executor(None, input, f"Are you sure you want to delete pod '{pod_name}'? [y/n] >> ")
                if confirm.lower() in ('y', 'yes'):
                    await self.delete_pod(pod_name)
                    print(f"Pod '{pod_name}' deleted.")
                else:
                    print("Operation aborted.")

            elif user_input.lower() in ('q', 'exit'):
                print("\nExiting admin pod management.")
                break

            else:
                print("\nInvalid option. Please try again.")

