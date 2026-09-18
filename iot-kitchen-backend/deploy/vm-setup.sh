#!/bin/bash
# =====================================================================
#  CHUẨN BỊ MÁY CHỦ - chạy MỘT LẦN sau khi tạo máy ảo
#  Dùng được cho Google Cloud, AWS EC2, DigitalOcean, Oracle Cloud (Ubuntu 24.04)
#
#  Cách chạy:
#      chmod +x deploy/vm-setup.sh
#      ./deploy/vm-setup.sh
#
#  Script làm 4 việc: tạo swap, cài Docker, mở tường lửa, tạo lệnh tắt "dc".
# =====================================================================
set -e

echo "==> 1/4  Tạo swap 4 GB"
# Máy e2-micro chỉ có 1 GB RAM. Lúc chạy thì đủ (đo thật: ~330 MB cho cả 5 container),
# nhưng lúc cài scikit-learn cho dịch vụ AI có thể ngốn tới 600 MB. Swap để không bị kill.
if [ ! -f /swapfile ]; then
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    # Ưu tiên dùng RAM thật, chỉ đụng swap khi gần hết
    sudo sysctl -w vm.swappiness=10
    echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf > /dev/null
else
    echo "    (đã có /swapfile, bỏ qua)"
fi
free -h

echo "==> 2/4  Cài Docker"
if ! command -v docker > /dev/null; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "    ĐÃ CÀI XONG. Phải đăng xuất rồi vào lại thì mới chạy docker không cần sudo."
else
    echo "    (đã có Docker $(docker --version))"
fi

echo "==> 3/4  Mở tường lửa trong máy"
# Các nhà cung cấp đều chặn ở 2 tầng: tường lửa trên bảng điều khiển và tường lửa trong máy.
# Bước này chỉ lo tầng trong máy. Tầng ngoài phải mở bằng tay:
#   Google Cloud -> VPC network > Firewall
#   AWS EC2      -> Security Group của instance
#   DigitalOcean -> không có, chỉ cần ufw
sudo ufw allow 22/tcp    > /dev/null   # SSH - để đầu tiên, quên là tự khoá mình ra ngoài
sudo ufw allow 80/tcp    > /dev/null   # Let's Encrypt xác minh tên miền
sudo ufw allow 443/tcp   > /dev/null   # https và wss
sudo ufw allow 1883/tcp  > /dev/null   # MQTT cho ESP32
sudo ufw --force enable
sudo ufw status numbered

echo "==> 4/4  Tạo lệnh tắt"
LINE="alias dc='docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml -f deploy/docker-compose.lowmem.yml'"
grep -qF "$LINE" ~/.bashrc || echo "$LINE" >> ~/.bashrc
echo "    Từ nay gõ 'dc ps', 'dc logs -f backend' thay cho lệnh dài."

echo ""
echo "XONG. Bước tiếp theo:"
echo "  1. Đăng xuất rồi SSH vào lại (để nhóm docker có hiệu lực)"
echo "  2. cp .env.example .env  &&  nano .env"
echo "  3. nano deploy/Caddyfile   (sửa tên miền ở dòng đầu)"
echo "  4. source ~/.bashrc  &&  dc up -d --build"
