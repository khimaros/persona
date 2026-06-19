# Debian Maintenance Advanced Reference

## 📂 Source List Management
- **List sources**: `cat /etc/apt/sources.list`
- **List additional sources**: `ls /etc/apt/sources.list.d/`
- **Add PPA (Ubuntu)**: `sudo add-apt-repository ppa:<user>/<ppa-name>`
- **Remove PPA**: `sudo add-apt-repository --remove ppa:<user>/<ppa-name>`

## 🔑 Key Management
- **List keys**: `apt-key list`
- **Add key from URL**: `wget -qO - <url> | sudo apt-key add -`
- **Modern way (Trusted.gpg.d)**: `wget -qO - <url> | gpg --dearmor | sudo tee /usr/share/keyrings/<name>.gpg > /dev/null`

## 📦 Advanced Package Operations
- **List files in package**: `dpkg -L <package>`
- **Find package for file**: `dpkg -S /path/to/file`
- **Check package status**: `dpkg -s <package>`
- **List all installed packages**: `dpkg --get-selections`
- **Hold package**: `sudo apt-mark hold <package>`
- **Unhold package**: `sudo apt-mark unhold <package>`

## 💾 System Cleanup (Deep)
- **Remove old kernels**: `sudo apt-get autoremove --purge`
- **Check disk space**: `df -h`
- **Check apt cache size**: `du -sh /var/cache/apt/archives`
