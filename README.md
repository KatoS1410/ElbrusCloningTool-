# Elbrus Cloning Tool

This tool restores system configuration and LXC environment from a prepared backup set. It is intended for a very specific system layout used in ElbrusOS environments.

---

## Important notice

This script is tightly coupled with ElbrusOS filesystem structure and operational assumptions. It is not designed for general Linux distributions.

Running it on other systems can result in broken networking, overwritten system configuration, loss of container data, and overall system instability.

Use only in environments where you fully understand the changes being applied.

---

## What the tool does

The script restores LXC containers from backup archives located in a specified directory.

It applies system configuration restoration including hostname, fstab entries, network configuration, nginx configuration, and other system-level files defined in the backup.

Before modifying anything, it creates emergency backups of existing files.

Most destructive operations require user confirmation during execution.

---

## Backup requirements

The input directory must contain exactly two archives:

backup_.tar.gz  
backup_conf_.tar.gz  

These archives are expected to contain container data and system configuration snapshots in a predefined internal structure.

---

## Usage

Run the script with Python 3:

python3 restore.py /path/to/backup_directory

If no path is provided, the script will prompt for input interactively.

---

## Behavior

The script runs interactively by default.

Before applying changes, it asks for confirmation.

Possible inputs:

yes - proceed with action  
no - stop execution immediately  
auto - enable automatic mode and proceed without further prompts  

---

## Safety notes

The script performs backups before overwriting system files.

Services related to networking and containers may be stopped during execution.

Root privileges are required for most operations.

iptables restoration has been intentionally removed due to lack of reliability and system-specific incompatibilities.

---

## Limitations

This tool works only with the expected ElbrusOS directory and backup structure.

Cross-distribution usage is not supported.

---

## Testing status

This version has not been fully validated in production environments.

It should be considered experimental and used with caution.

---

## License

Internal use tool. No public license defined.
