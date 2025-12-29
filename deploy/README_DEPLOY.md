# Deploy

1. Скопировать архив на сервер:
   scp tg-catalog-layered-final-full.tar.gz root@SERVER:/root/

2. На сервере:
   cd /root
   rm -rf tg-catalog-layered-final
   tar xzf tg-catalog-layered-final-full.tar.gz
   bash /root/tg-catalog-layered-final/deploy/deploy_inplace.sh
