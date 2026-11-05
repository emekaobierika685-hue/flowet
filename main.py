import os
import concurrent.futures
import uuid
import requests
import threading
import time
from colorama import Fore, Style, init

init(autoreset=True)

# --- CONFIGURATION ---
MAX_WORKERS = 100  # How many requests happen at the exact same time
USE_PROXY = False  # Set to True if you want to use proxies later

class DiscordNitroGen:
    def __init__(self):
        self.nitro_file_path = "nitros.txt"
        self._lock = threading.Lock()
        self._worked = 0
        self._failed = 0

    def run(self):
        print(f"{Fore.CYAN}Starting ENDLESS Nitro Generator...{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Press Ctrl+C or close this window to stop.{Style.RESET_ALL}\n")
        
        with open(self.nitro_file_path, "a", buffering=1) as f:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # We submit an initial batch of workers
                futures = {executor.submit(self.generate_nitro) for _ in range(MAX_WORKERS)}
                
                try:
                    while True:
                        # as_completed waits for ANY worker to finish
                        for future in concurrent.futures.as_completed(futures):
                            try:
                                has_generated, response_text = future.result()
                            except Exception as e:
                                has_generated, response_text = False, str(e)
                            
                            with self._lock:
                                if has_generated:
                                    self._worked += 1
                                    f.write(response_text + "\n")
                                else:
                                    self._failed += 1
                                
                                color = Fore.GREEN if has_generated else Fore.RED
                                label = "SUCCESS" if has_generated else "FAILED "
                                msg = response_text if len(response_text) <= 70 else response_text[:67] + "..."
                                
                                print(f"{color}Worked: {self._worked} | Failed: {self._failed} | {label} | {msg}{Style.RESET_ALL}")
                            
                            # Remove finished future from the set
                            futures.remove(future)
                            
                            # Immediately submit a new one to keep it endless!
                            futures.add(executor.submit(self.generate_nitro))
                            
                except KeyboardInterrupt:
                    print(f"\n{Fore.YELLOW}Stopping... Please wait for active requests to finish.{Style.RESET_ALL}")
                    executor.shutdown(wait=False, cancel_futures=True)

    def generate_nitro(self):
        req_headers = {
            'Accept': '*/*',
            'Authority': 'api.discord.gx.games',
            'Accept-Language': 'en-US',
            'Origin': 'https://www.opera.com',
            'Referer': 'https://www.opera.com/',
            'Sec-ch-ua': '"Opera";v="105", "Chromium";v="119", "Not?A_Brand";v="24"',
            'Sec-ch-ua-mobile': '?0',
            'Sec-ch-ua-platform': '"Windows"',
            'Sec-fetch-dest': 'empty',
            'Sec-fetch-mode': 'cors',
            'Sec-fetch-site': 'cross-site',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0',
        }
        req_json = {"partnerUserId": str(uuid.uuid4())}

        try:
            result = requests.post(
                "https://api.discord.gx.games/v1/direct-fulfillment",
                headers=req_headers,
                json=req_json,
                timeout=10
            )
            
            if result.status_code != 200:
                return False, f"HTTP {result.status_code}"
                
            token = result.json().get("token")
            if not token:
                return False, "No token in response"
                
            return True, "https://discord.com/billing/partner-promotions/1180231712274387115/" + token

        except Exception as e:
            return False, str(e)

if __name__ == "__main__":
    generator = DiscordNitroGen()
    generator.run()