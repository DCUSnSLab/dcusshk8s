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

        if search_string is not None:
            pods_list.items = [pod for pod in pods_list.items if search_string in pod.metadata.name]
            print(f"\nSearching for '{search_string}' in '{self.namespace}' namespace")
        else:
            print(f"\nPods in '{self.namespace}' namespace")
        print("-" * 50)

        if pods_list.items:
            print(f"{'NUM':<4} {'NAME':<30} {'STATUS'}")
            for idx, pod in enumerate(pods_list.items, start=1):
                pod_name = pod.metadata.name
                pod_status = pod.status.phase
                if pod.metadata.deletion_timestamp is not None: pod_status = "Terminating"
                print(f"{idx:<4} {pod_name:<30} {pod_status}")
        else:
            print("Not found")
        print("=" * 50)


    async def create_pod(self, pod_name):
        print("\n", "-" * 50, sep="")
        pod = UserPod(parent=self, username=pod_name, namespace=self.namespace)
        async for status in pod.ensure_running():
            pass
            #if status == PodState.RUNNING:
                #print(f"Pod {pod_name} is running")
            #elif status == PodState.STARTING:
                #print(f"Pod '{pod_name}' created")
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


    async def interactive_input(self):
        while True:
            print("\nEnter '1' to list all pods, '2' to search for pods, '3' to create pod, '4' to delete pod.")
            user_input = await asyncio.get_event_loop().run_in_executor(None, input)
            user_input = user_input.strip()

            if user_input == '1':
                self.list_pods()
            elif user_input == '2':
                print("Enter search username >> ", end="")
                search_string = await asyncio.get_event_loop().run_in_executor(None, input)
                self.list_pods(search_string)
            elif user_input == '3':
                print("Enter pod name >> ", end="")
                pod_name = await asyncio.get_event_loop().run_in_executor(None, input)
                await self.create_pod(pod_name)
            elif user_input == '4':
                print("Enter pod name to delete >> ", end="")
                pod_name = await asyncio.get_event_loop().run_in_executor(None, input)
                await self.delete_pod(pod_name)
