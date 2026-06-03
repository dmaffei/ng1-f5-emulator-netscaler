# NetScaler example configuration

Sample CLI commands to seed test VIPs on NetScaler CPX or VPX.

## Login

```bash
docker exec -it netscaler-cpx cli_script.sh "show version"
```

## Create test VIP + service-group + members

```
add server srv1 192.168.30.1
add server srv2 192.168.30.104

add servicegroup sg-WEB-80 HTTP
bind servicegroup sg-WEB-80 srv1 80
bind servicegroup sg-WEB-80 srv2 80

add lb vserver vs-WEB-80 HTTP 192.168.30.35 8080
bind lb vserver vs-WEB-80 sg-WEB-80
```

## Verify via NITRO

```bash
TOK=$(curl -sk -X POST -H "Content-Type: application/json" \
  -d '{"login":{"username":"nsroot","password":"nsroot"}}' \
  http://192.168.30.35:9080/nitro/v1/config/login | jq -r .sessionid)

curl -sk -H "Cookie: sessionid=$TOK" \
  http://192.168.30.35:9080/nitro/v1/config/lbvserver
```

## Verify via shim

```bash
curl -sk -u admin:netscout1 \
  https://192.168.30.32:8446/mgmt/tm/ltm/virtual
```
