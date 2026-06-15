# -*- coding: utf-8 -*-

import os
import sys
import tarfile
import shutil
import subprocess
import time
import logging
import re
from datetime import datetime, timedelta

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

file_handler = logging.FileHandler('/var/log/restore-kvm.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

DRY_RUN = False

CRITICAL_PATHS = ["/", "/etc", "/var", "/boot", "/usr"]


def is_safe_path(path: str):
    abs_path = os.path.abspath(path)
    return abs_path not in CRITICAL_PATHS


def safe_run(cmd):
    if DRY_RUN:
        print("[DRY_RUN]", cmd)
        return 0
    return subprocess.call(cmd)


def atomic_write(path, content):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


class BackupHandler(object):
    def __init__(self, backup_dir):
        self.backup_dir = os.path.abspath(backup_dir)
        self.temp_dir = os.path.join(self.backup_dir, 'temp_extract')
        self.emergency_backup_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'emergency_backup'
        )
        self.containers_dir = None
        self.conf_dir = None
        self.auto_mode = False

    def setup_dirs(self):
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.emergency_backup_dir, exist_ok=True)

    def find_archives(self):
        archives = [f for f in os.listdir(self.backup_dir) if f.endswith('.tar.gz')]
        if len(archives) != 2:
            raise ValueError("Expected exactly 2 archives")

        backup_arch = [f for f in archives if f.startswith('backup_')]
        conf_arch = [f for f in archives if f.startswith('backup_conf_')]

        if len(backup_arch) != 1 or len(conf_arch) != 1:
            raise ValueError("Invalid archive set")

        return (
            os.path.join(self.backup_dir, backup_arch[0]),
            os.path.join(self.backup_dir, conf_arch[0])
        )

    def extract_nested_tar(self, archive_path, target_name):
        with tarfile.open(archive_path, 'r:gz') as tar:
            tar.extractall(path=self.temp_dir)

        inner_dir = os.path.join(self.temp_dir, '_', 'tmp')
        inner_tar = os.path.join(inner_dir, target_name)

        if not os.path.exists(inner_tar):
            raise ValueError("Invalid archive structure")

        extract_path = os.path.join(self.temp_dir, target_name.split('.')[0])

        with tarfile.open(inner_tar, 'r') as inner:
            inner.extractall(path=extract_path)

        return extract_path

    def backup_file(self, src_path):
        if not os.path.exists(src_path):
            return

        dest = os.path.join(
            self.emergency_backup_dir,
            os.path.basename(src_path) + "_" + datetime.now().strftime('%Y%m%d_%H%M%S')
        )
        shutil.copy2(src_path, dest)

    def backup_dir(self, src_dir):
        if not os.path.exists(src_dir):
            return

        dest = os.path.join(
            self.emergency_backup_dir,
            os.path.basename(src_dir) + "_" + datetime.now().strftime('%Y%m%d_%H%M%S')
        )
        shutil.copytree(src_dir, dest)

    def confirm_action(self, message):
        if self.auto_mode:
            return True

        while True:
            resp = input(message + " (yes/no/auto): ").strip().lower()

            if resp == "yes":
                return True
            if resp == "no":
                return False
            if resp == "auto":
                self.auto_mode = True
                return True


class LxcRestorer(BackupHandler):

    def stop_containers(self):
        safe_run(['lxc-autostart', '-Aast', '120'])

    def restore_lxc(self):
        lxc_dir = '/var/lib/lxc'

        if not is_safe_path(lxc_dir):
            raise Exception("Unsafe path blocked")

        if not os.path.exists(self.containers_dir):
            raise ValueError("Missing containers dir")

        current = os.listdir(lxc_dir)
        new = os.listdir(self.containers_dir)

        if self.confirm_action(f"Replace LXC?\nCurrent: {current}\nNew: {new}"):
            self.stop_containers()

            for item in os.listdir(lxc_dir):
                path = os.path.join(lxc_dir, item)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

            for cont in new:
                shutil.copytree(
                    os.path.join(self.containers_dir, cont),
                    os.path.join(lxc_dir, cont)
                )


class FstabRestorer(BackupHandler):

    def restore_fstab_root_uuid(self):
        dest = "/etc/fstab"
        self.backup_file(dest)

        with open(dest) as f:
            lines = f.readlines()

        if self.confirm_action("Replace fstab root entry?"):
            atomic_write(dest, "".join(lines))


    def restore_fstab_storage_mounts(self):
        pass


class NetworkRestorer(BackupHandler):

    def restore_hostname(self):
        dest = "/etc/sysconfig/network"
        self.backup_file(dest)

        if self.confirm_action("Replace hostname?"):
            atomic_write(dest, "HOSTNAME=restored\n")

    def restore_interfaces(self):
        pass

    def restore_nginx(self):
        pass

    def restore_other_conf(self):
        pass

    def restart_network(self):
        print("Network changes require manual restart")


class MainRestorer(object):

    def __init__(self, backup_dir):
        self.backup_handler = BackupHandler(backup_dir)
        self.lxc = LxcRestorer(backup_dir)
        self.fstab = FstabRestorer(backup_dir)
        self.net = NetworkRestorer(backup_dir)

    def run(self):

        self.backup_handler.setup_dirs()
        self.backup_handler.extract_backups()

        self.net.restore_hostname()
        self.fstab.restore_fstab_root_uuid()
        self.lxc.restore_lxc()

        print("Done")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: script.py /backup/path")
        sys.exit(1)

    MainRestorer(sys.argv[1]).run()
