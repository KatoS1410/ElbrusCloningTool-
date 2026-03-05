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

# Логи
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler('/var/log/restore-kvm.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

class BackupHandler(object):
    def __init__(self, backup_dir):
        self.backup_dir = os.path.abspath(backup_dir)
        self.temp_dir = os.path.join(self.backup_dir, 'temp_extract')
        self.emergency_backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'emergency_backup')
        self.containers_dir = None
        self.conf_dir = None
        self.auto_mode = False

    def setup_dirs(self):
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        if not os.path.exists(self.emergency_backup_dir):
            os.makedirs(self.emergency_backup_dir)

    def find_archives(self):
        archives = [f for f in os.listdir(self.backup_dir) if f.endswith('.tar.gz')]
        if len(archives) != 2:
            raise ValueError("Expected exactly 2 archives in the directory. Found: %s" % len(archives))
        backup_arch = [f for f in archives if f.startswith('backup_')]
        conf_arch = [f for f in archives if f.startswith('backup_conf_')]
        if len(backup_arch) != 1 or len(conf_arch) != 1:
            raise ValueError("Missing required archives: backup_* and backup_conf_*")
        return os.path.join(self.backup_dir, backup_arch[0]), os.path.join(self.backup_dir, conf_arch[0])

    def extract_nested_tar(self, archive_path, target_name):
        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                tar.extractall(path=self.temp_dir)
            inner_dir = os.path.join(self.temp_dir, '_', 'tmp')
            if not os.path.exists(inner_dir):
                raise ValueError("Archive does not have expected structure: _/tmp/")
            inner_tar = os.path.join(inner_dir, target_name)
            if not os.path.exists(inner_tar):
                raise ValueError(f"{target_name} not found in _/tmp/")
            extract_path = os.path.join(self.temp_dir, target_name.split('.')[0])
            with tarfile.open(inner_tar, 'r') as inner:
                inner.extractall(path=extract_path)
            return extract_path
        except ValueError as e:
            print(f"Error: {e}")
            logger.error(str(e))
            sys.exit(1)  # Грациозный выход с пояснением

    def extract_backups(self):
        backup_arch, conf_arch = self.find_archives()
        self.containers_dir = self.extract_nested_tar(backup_arch, 'backup_containers.tar')
        self.conf_dir = self.extract_nested_tar(conf_arch, 'backup_conf.tar')

    def cleanup(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def backup_file(self, src_path):
        if os.path.exists(src_path):
            dest = os.path.join(self.emergency_backup_dir, os.path.basename(src_path) + '_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
            shutil.copy2(src_path, dest)
            logger.debug("Backed up %s to %s" % (src_path, dest))
            print("Backed up %s" % src_path)

    def backup_dir(self, src_dir):
        if os.path.exists(src_dir):
            dest = os.path.join(self.emergency_backup_dir, os.path.basename(src_dir) + '_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
            shutil.copytree(src_dir, dest)
            logger.debug("Backed up dir %s to %s" % (src_dir, dest))
            print("Backed up dir %s" % src_dir)

    def should_replace(self, src_content, dest_path):
        if not os.path.exists(dest_path):
            return True
        with open(dest_path, 'r') as f:
            dest_content = f.read()
        return src_content != dest_content

    def confirm_action(self, message):
        if self.auto_mode:
            return True
        while True:
            resp = raw_input(message + " (Yes/No/Auto): ").strip().lower()
            if resp in ['yes', 'y']:
                return True
            elif resp in ['no', 'n']:
                print("Aborting script due to No response.")
                logger.info("Aborted on No")
                sys.exit(1)
            elif resp == 'auto':
                self.auto_mode = True
                return True
            print("Invalid input. Try again.")

class LxcRestorer(BackupHandler):
    def stop_containers(self):
        print("Stopping LXC containers...")
        logger.info("Initiating LXC stop")
        subprocess.call(['lxc-autostart', '-Aast', '120'])
        for _ in range(2):  # Two attempts, 3 min each
            time.sleep(180)
            output = subprocess.check_output(['lxc-ls', '-f'])
            if 'RUNNING' not in output:
                logger.info("All containers stopped")
                return
        print("Forcing stop...")
        subprocess.call(['service', 'lxc', 'stop'])
        logger.info("Forced LXC stop")

    def restore_lxc(self):
        lxc_dir = '/var/lib/lxc'
        if not os.path.exists(self.containers_dir):
            raise ValueError("Containers dir not extracted")
        # List current and new
        current_conts = os.listdir(lxc_dir)
        new_conts = os.listdir(self.containers_dir)
        msg = "Current LXC containers: %s\nNew from backup: %s\nReplace?" % (', '.join(current_conts), ', '.join(new_conts))
        if self.confirm_action(msg):
            self.stop_containers()
            for item in os.listdir(lxc_dir):
                path = os.path.join(lxc_dir, item)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            logger.info("Cleared /var/lib/lxc")

        # Копирование ящиков
        for cont in new_conts:
            src = os.path.join(self.containers_dir, cont)
            dest = os.path.join(lxc_dir, cont)
            if os.path.isdir(src):
                shutil.copytree(src, dest)
                logger.info("Copied container %s" % cont)
                print("Copied %s" % cont)

        # Делаем подпапочки
        subdirs = ['rootfs', 'data-01', 'logs']
        for cont in os.listdir(lxc_dir):
            cont_dir = os.path.join(lxc_dir, cont)
            missing_subs = [sub for sub in subdirs if not os.path.exists(os.path.join(cont_dir, sub))]
            if missing_subs:
                msg = "Missing subdirs in %s: %s\nCreate?" % (cont, ', '.join(missing_subs))
                if self.confirm_action(msg):
                    for sub in missing_subs:
                        sub_path = os.path.join(cont_dir, sub)
                        os.makedirs(sub_path)
                        logger.info("Created %s in %s" % (sub, cont))
                        print("Created %s" % sub)

        # Разрешение
        msg = "Set chown -R 100000:100000 on all LXC dirs?"
        if self.confirm_action(msg):
            subprocess.call(['chown', '-R', '100000:100000', lxc_dir])
            logger.info("Set permissions on LXC")
            print("Permissions set")

class FstabRestorer(BackupHandler):
    def get_current_root_line(self):
        try:
            root_dev = subprocess.check_output(['df', '/']).splitlines()[1].split()[0]
            blkid_line = subprocess.check_output(['blkid', root_dev]).strip()
            uuid_match = re.search(r'UUID="([^"]+)"', blkid_line)
            type_match = re.search(r'TYPE="([^"]+)"', blkid_line)
            if not uuid_match or not type_match:
                raise ValueError("Cannot parse UUID or FS type from blkid")
            uuid = uuid_match.group(1)
            fstype = type_match.group(1)
            return 'UUID=%s / %s defaults 1 1\n' % (uuid, fstype), root_dev
        except Exception as e:
            raise ValueError("Failed to get current root UUID/fstype: %s" % e)

    def restore_fstab_root_uuid(self):
        dest = '/etc/fstab'
        self.backup_file(dest)
        with open(dest, 'r') as f:
            lines = f.readlines()

        current_root_line = next((l for l in lines if l.strip().startswith('UUID=') and '/' in l.split() and l.split()[1] == '/'), None)
        if not current_root_line:
            print("Warning: root mount line not found in current fstab")
            return

        new_root_line, root_dev = self.get_current_root_line()
        msg = "Current root line:\n%sNew root line (actual UUID + fstype from %s):\n%sReplace?" % (current_root_line.strip(), root_dev, new_root_line.strip())
        if current_root_line.strip() != new_root_line.strip():
            if self.confirm_action(msg):
                new_lines = [new_root_line if l == current_root_line else l for l in lines]
                with open(dest, 'w') as f:
                    f.writelines(new_lines)
                logger.info("Updated root UUID line in fstab")
                print("Root UUID line updated")
        else:
            print("Root UUID line already correct")

    def restore_fstab_storage_mounts(self):
        fstab_src = os.path.join(self.conf_dir, 'fstab')
        if not os.path.exists(fstab_src):
            raise ValueError("fstab not in backup")
        dest = '/etc/fstab'
        self.backup_file(dest)

        with open(fstab_src, 'r') as f:
            backup_lines = f.readlines()
        with open(dest, 'r') as f:
            current_lines = f.readlines()

        backup_storage = [l.strip() for l in backup_lines if l.startswith('/dev/storage')]
        current_storage = [l.strip() for l in current_lines if l.startswith('/dev/storage')]

        if not backup_storage:
            print("No /dev/storage mounts in backup fstab")
            return

        msg = "Current /dev/storage mounts:\n%s\nNew from backup:\n%s\nReplace storage mounts?" % (
            '\n'.join(current_storage) if current_storage else "None",
            '\n'.join(backup_storage)
        )
        if set(backup_storage) != set(current_storage):
            if self.confirm_action(msg):
                # Удаляем старые стораджи, вставляем новые
                new_lines = [l for l in current_lines if not l.startswith('/dev/storage')]
                # Вставка
                insert_pos = len(new_lines)
                for i, line in enumerate(new_lines):
                    if line.startswith('UUID=') and '/' in line.split() and line.split()[1] == '/':
                        insert_pos = i + 1
                        break
                new_lines = new_lines[:insert_pos] + [s + '\n' for s in backup_storage] + new_lines[insert_pos:]
                with open(dest, 'w') as f:
                    f.writelines(new_lines)
                logger.info("Replaced /dev/storage mounts in fstab")
                print("Storage mounts replaced in fstab")
        else:
            print("Storage mounts already match")

class NetworkRestorer(BackupHandler):
    def restore_hostname(self):
        hostname_src = os.path.join(self.conf_dir, 'hostname.txt')
        if not os.path.exists(hostname_src):
            raise ValueError("hostname.txt not found in backup_conf")
        with open(hostname_src, 'r') as f:
            new_hostname = f.read().strip()

        dest = '/etc/sysconfig/network'
        self.backup_file(dest)
        current_hostname = "unknown"
        if os.path.exists(dest):
            with open(dest, 'r') as f:
                for line in f:
                    if line.startswith('HOSTNAME='):
                        current_hostname = line.split('=', 1)[1].strip()
                        break

        msg = "Current HOSTNAME: %s\nNew from backup: %s\nReplace?" % (current_hostname, new_hostname)
        new_line = "HOSTNAME=%s\n" % new_hostname
        if self.should_replace(new_line, dest) or current_hostname != new_hostname:
            if self.confirm_action(msg):
                with open(dest, 'w') as f:
                    f.write(new_line)
                logger.info("Hostname set to %s" % new_hostname)
                print("Hostname set to %s" % new_hostname)

    def restore_interfaces(self):
        sysconfig_src = os.path.join(self.conf_dir, 'sysconfig')
        if not os.path.exists(sysconfig_src):
            print("sysconfig backup not found — skipping network config")
            return

        target = '/etc/sysconfig/network-devices'
        self.backup_dir(target)

        for iface in ['eth0', 'br0']:
            src_dir = os.path.join(sysconfig_src, 'network-devices', f'ifcfg.{iface}')
            if not os.path.exists(src_dir):
                raise ValueError(f"ifcfg.{iface} not found in backup — stopping")
            dest_dir = os.path.join(target, f'ifcfg.{iface}')
            current_files = os.listdir(dest_dir) if os.path.exists(dest_dir) else []
            new_files = os.listdir(src_dir)

            msg = f"Replace entire interface config {iface}?\nCurrent files: {', '.join(current_files)}\nNew files from backup: {', '.join(new_files)}\nThis includes ipv4 and all route_* files\nReplace?"
            if self.confirm_action(msg):
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.copytree(src_dir, dest_dir)
                logger.info(f"Replaced network interface config: {iface}")
                print(f"Interface {iface} fully restored (including routes)")

    def restore_iptables(self):
        ipt_src = os.path.join(self.conf_dir, 'iptables.txt')
        if os.path.exists(ipt_src):
            with open(ipt_src, 'r') as f:
                new_content = f.read()
            current_content = subprocess.check_output(['iptables-save'])
            msg = "Current iptables: %s\nNew from backup: %s\nReplace?" % (current_content.strip(), new_content.strip())
            if self.confirm_action(msg):
                if not new_content.strip():
                    subprocess.call(['iptables', '-F'])
                    logger.info("Flushed iptables")
                    print("Iptables flushed")
                else:
                    temp_file = '/tmp/ipt.txt'
                    with open(temp_file, 'w') as f:
                        f.write(new_content)
                    subprocess.call(['iptables-restore', '<', temp_file])
                    os.remove(temp_file)
                    logger.info("Restored iptables")
                    print("Iptables restored")

    def restore_nginx(self):
        nginx_src = os.path.join(self.conf_dir, 'nginx')
        if not os.path.exists(nginx_src):
            return
        target = '/etc/nginx'
        # Собираем инфу
        new_files = []
        for root, dirs, files in os.walk(nginx_src):
            for file in files:
                src_file = os.path.join(root, file)
                rel_path = os.path.relpath(src_file, nginx_src)
                dest_file = os.path.join(target, rel_path)
                mod_time = datetime.fromtimestamp(os.path.getmtime(src_file))
                if (file.endswith('.pem') or file.endswith('.crt')) and (datetime.now() - mod_time > timedelta(days=120)):
                    print("Skipping old cert %s" % rel_path)
                    continue
                with open(src_file, 'r') as f:
                    content = f.read()
                current_content = open(dest_file).read() if os.path.exists(dest_file) else "None"
                new_files.append("%s: current '%s...' -> new '%s...'" % (rel_path, current_content[:20], content[:20]))

        msg = "Nginx changes:\n%s\nReplace?" % '\n'.join(new_files)
        if self.confirm_action(msg):
            for root, dirs, files in os.walk(nginx_src):
                for file in files:
                    src_file = os.path.join(root, file)
                    rel_path = os.path.relpath(src_file, nginx_src)
                    dest_file = os.path.join(target, rel_path)
                    mod_time = datetime.fromtimestamp(os.path.getmtime(src_file))
                    if (file.endswith('.pem') or file.endswith('.crt')) and (datetime.now() - mod_time > timedelta(days=120)):
                        continue
                    self.backup_file(dest_file)
                    shutil.copy2(src_file, dest_file)
                    logger.info("Replaced nginx %s" % rel_path)
                    print("Nginx %s replaced" % rel_path)

    def restore_other_conf(self):
        files = ['resolv.conf', 'chrony.conf', 'sysctl.conf', 'host']
        for file in files:
            src = os.path.join(self.conf_dir, file)
            if os.path.exists(src):
                dest = '/etc/' + file if file != 'host' else '/etc/hosts'
                self.backup_file(dest)
                with open(src, 'r') as f:
                    new_content = f.read()
                current_content = open(dest).read() if os.path.exists(dest) else "None"
                msg = "%s:\nCurrent (first 100 chars):\n%s\nNew from backup:\n%s\nReplace?" % (
                    dest, current_content[:100], new_content[:100]
                )
                if self.should_replace(new_content, dest):
                    if self.confirm_action(msg):
                        with open(dest, 'w') as f:
                            f.write(new_content)
                        logger.info("Replaced %s" % dest)
                        print("%s replaced" % dest)

    def restart_network(self):
        print("Network config ready. Run 'service network restart' manually if needed.")
        logger.info("Network ready for restart")

class MainRestorer(object):
    def __init__(self, backup_dir):
        self.backup_handler = BackupHandler(backup_dir)
        self.lxc_restorer = LxcRestorer(backup_dir)
        self.fstab_restorer = FstabRestorer(backup_dir)
        self.network_restorer = NetworkRestorer(backup_dir)

    def run(self):
        try:
            print("Йоу, это скрипт автоматического клонирования с посредником. Где лежат бэкапы, укажи полный путь начиная с /")
            print("Предупреждение: тебе надо указать папку где лежат 2 архива с бэкапами типа backup_conf или просто backup_имярегиона. Не надо указывать путь до архива, надо указать путь только до папки где они лежат.")
            print("Лучше создай отдельную папку и забрось эти два архива туда и укажи путь до него. Например, /tmp/restore_kvm01/")
            print("Итак, поехали. Твой путь:")
            self.backup_handler.setup_dirs()
            self.backup_handler.extract_backups()

            # КРИТИЧЕСКИ ВАЖНЫЙ ШАГ ИЗ ИНСТРУКЦИИ
            udev_rules = '/etc/udev/rules.d/70-persistent-net.rules'
            if os.path.exists(udev_rules):
                print(f"Удаляем старое правило udev: {udev_rules}")
                self.backup_handler.backup_file(udev_rules)
                os.remove(udev_rules)
                logger.info("Removed 70-persistent-net.rules — prevents interface name conflicts")
            else:
                print("70-persistent-net.rules не найден — ок, уже чисто")

            # Шаги рестора
            self.network_restorer.restore_hostname()
            self.network_restorer.restore_other_conf()
            self.network_restorer.restore_nginx()
            self.network_restorer.restore_interfaces()
            self.network_restorer.restore_iptables()
            self.network_restorer.restart_network()

            self.fstab_restorer.restore_fstab_root_uuid()
            self.fstab_restorer.restore_fstab_storage_mounts()

            self.lxc_restorer.restore_lxc()

            print("Все изменения применены. Напишите reboot и изменения применятся.")
        except Exception as e:
            logger.error("Error: %s" % e)
            print("Error: %s" % e)
        finally:
            self.backup_handler.cleanup()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        backup_dir = sys.argv[1]
    else:
        backup_dir = raw_input("Enter path to backups dir: ").strip()
    restorer = MainRestorer(backup_dir)
    restorer.run()