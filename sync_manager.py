"""
Multiprocess sync manager with progress tracking
"""

import multiprocessing
from dataclasses import dataclass
from typing import List, Optional
from tqdm import tqdm
import time

from imap_client import EmailImapClient
from cache import EmailCache
DEFAULT_CACHE_DAYS = 90


@dataclass
class SyncResult:
    folder: str
    success: bool
    new: int = 0
    updated: int = 0
    expunged: int = 0
    error: Optional[str] = None
    output: str = ""


class SyncManager:
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.results: List[SyncResult] = []

    @classmethod
    def worker(cls, args):
        shared_results, config, account, folder = args

        cache = EmailCache(account, config.get('cache_days', DEFAULT_CACHE_DAYS))
        imap_host, imap_port, email_addr = config.get('imap_host'), config.get('imap_port'), config.get('email')
        password = config.get_password()
        email_client = EmailImapClient(imap_host, imap_port, email_addr, password, cache)

        result = SyncManager.sync_folder(email_client, folder, page_size=100)

        email_client.close()

        shared_results.append(result)

        time.sleep(0.5)

    @classmethod
    def sync_folder(cls, email_client, folder: str, page_size: int) -> SyncResult:
        """Sync a single folder"""

        result = SyncResult(folder=folder, success=False)

        try:
            if True:
                # Run sync
                stats = email_client.sync_from_server(folder, page_size)

                # Extract stats if returned
                if stats:
                    result.new = stats.get('new', 0)
                    result.updated = stats.get('updated', 0)
                    result.expunged = stats.get('expunged', 0)

            result.success = True

        except Exception as e:
            result.success = False
            result.error = str(e)

        return result

    def sync_all_folders(self, config, account, folders: List[str], page_size: int):
        """Sync all folders with progress bar"""

        with multiprocessing.Manager() as manager:
            shared_list = manager.list()

            def generate_args(folders):
                return [(shared_list, config, account, folder,) for (i, folder) in enumerate(folders)]

            with multiprocessing.Pool(self.max_workers) as pool:
                list(tqdm(pool.imap(SyncManager.worker, generate_args(folders)), total=len(folders)))

            self.results.extend(shared_list)

        return self.results

    def print_summary(self):
        """Print summary of all sync results"""
        print("\n" + "="*60)
        print("Sync Summary")
        print("="*60)

        for result in sorted(self.results, key=lambda r: r.folder):
            if result.success:
                print(f"[{result.folder}] Sync complete! "
                      f"New: {result.new}, Updated: {result.updated}, Expunged: {result.expunged}")
            else:
                print(f"[{result.folder}] FAILED: {result.error}")

        # Overall stats
        total_new = sum(r.new for r in self.results if r.success)
        total_updated = sum(r.updated for r in self.results if r.success)
        total_expunged = sum(r.expunged for r in self.results if r.success)
        failed = sum(1 for r in self.results if not r.success)

        print("="*60)
        print(f"Total: New: {total_new}, Updated: {total_updated}, Expunged: {total_expunged}")
        if failed:
            print(f"Failed: {failed} folder(s)")
        print("="*60)
