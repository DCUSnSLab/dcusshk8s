from kubernetes import client, config
import asyncio

class PodManager:
    def __init__(self, namespace="default"):
        config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.namespace = namespace

    def list_pods(self):
        pods = self.v1.list_namespaced_pod(self.namespace)
        print(f"\nPods in '{self.namespace}' namespace")
        print("-" * 50)
        for idx, pod in enumerate(pods.items, start=1):
            print(f"[{idx}] {pod.metadata.name}")
        print("=" * 50)

    def search_pods(self, search_string):
        pods = self.v1.list_namespaced_pod(self.namespace)
        print(f"\nSearching for '{search_string}' in '{self.namespace}' namespace")
        print("-" * 50)
        found_pods = [pod.metadata.name for pod in pods.items if search_string in pod.metadata.name]
        if found_pods:
            for idx, pod in enumerate(found_pods, start=1):
                print(f"[{idx}] {pod}")
        else:
            print("Not found")
        print("=" * 50)

    async def interactive_input(self):
        while True:
            print("\nEnter '1' to list all pods, '2' to search for pods")
            user_input = await asyncio.get_event_loop().run_in_executor(None, input)
            user_input = user_input.strip()

            if user_input == '1':
                self.list_pods()
            elif user_input == '2':
                print("Enter search username >> ", end="")
                search_string = await asyncio.get_event_loop().run_in_executor(None, input)
                self.search_pods(search_string)

