# Развертывание на сервере

1. Локально собрать архив:

   ```bash
   cd ~/Downloads
   tar \
     --exclude='venv' \
     --exclude='.venv' \
     --exclude='__pycache__' \
     -cf tg-catalog-layered-final.tar tg-catalog-layered-final
   ```

2. Скопировать архив на сервер:

   ```bash
   scp tg-catalog-layered-final.tar root@217.199.252.10:/root/
   ```

3. Зайти на сервер:

   ```bash
   ssh root@217.199.252.10
   ```

4. Запустить деплой:

   ```bash
   cd /root
   tar xf tg-catalog-layered-final.tar
   cd tg-catalog-layered-final
   chmod +x deploy/deploy_from_archive.sh
   cd /root
   ./tg-catalog-layered-final/deploy/deploy_from_archive.sh
   ```
