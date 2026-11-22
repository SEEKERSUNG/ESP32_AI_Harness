# CA certificates

| 文件 | 说明 |
|---|---|
| `ca-bundle.pem` | Mozilla CA 全量根证书 bundle，适合会更换 API 地址或访问多个 HTTPS 服务的部署 |
| `digicert-global-root-g2.pem` | 最小根证书，仅用于固定 DigiCert 链的 DeepSeek 端点 |

`ca-bundle.pem` 来源：

```text
https://curl.se/ca/cacert.pem
```

当前内容：

- Mozilla 数据日期：2026-11-05
- 根证书数量：118
- 文件大小：200116 字节
- SHA-256：`427C064216761A1E72A8B30F0D954D08CB6E4435A82091FB865AFD20C8497285`

## 安装

```powershell
python tools/force_raw.py --port <PORT>
mpremote connect <PORT> fs cp certs/ca-bundle.pem :ca.pem
mpremote connect <PORT> reset
```

设备配置中的 `ca_file` 保持 `/ca.pem` 即可。全量 bundle 已安装后，通常不需要再在 Web 或串口修改 CA：保持 TLS 验证开启和当前路径，Web 的“高级：自定义 CA”保持收起；只有私有 CA、企业内网或自建 API 才需要展开修改。

## 切换回单一根证书

```powershell
python tools/force_raw.py --port <PORT>
mpremote connect <PORT> fs cp certs/digicert-global-root-g2.pem :ca.pem
mpremote connect <PORT> reset
```

## 注意事项

- 全量 bundle 会信任 Mozilla 收录的所有公共根 CA，兼容性更好，但信任面比固定单一根证书更大。
- 企业内网、自建 API 或私有 CA 仍需单独把对应根证书加入 `ca.pem`。
- 全量 bundle 只解决公共根证书信任；设备仍需正确的 RTC/NTP 时间，否则会报证书尚未生效或已过期。
- 更新 bundle 后建议执行 `/test` 和 `/test api`，确认 Wi-Fi、时间和 TLS 均正常。
