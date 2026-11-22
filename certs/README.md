# CA certificates

| 文件 | 说明 |
|---|---|
| `ca-bundle.pem` | Mozilla CA 全量根证书 bundle，兼容多个公共 HTTPS 端点 |

`ca-bundle.pem` 来源：

```text
https://curl.se/ca/cacert.pem
```

当前内容：

- Mozilla 数据日期：2026-11-05
- 根证书数量：118
- 文件大小：200116 字节
- SHA-256：`427C064216761A1E72A8B30F0D954D08CB6E4435A82091FB865AFD20C8497285`

## 部署时自动安装

正常部署会把全量 bundle 复制到设备 `/ca.pem`：

```powershell
python tools/deploy.py --port <PORT>
```

`--clean` 和首次增量部署都会包含 `ca.pem`；`--source` 只影响 Python 文件格式，不影响 CA；`--changed` 只在本地 `certs/ca-bundle.pem` 内容变化时重新复制。设备配置中的 `ca_file` 保持 `/ca.pem` 即可。

部署后通常不需要再在 Web 或串口修改 CA：保持 TLS 验证开启和当前路径，Web 的“高级：自定义 CA”保持收起；只有私有 CA、企业内网、自建 API 或手动恢复时才需要替换。

## 手动安装或恢复

设备文件被清空、部署器无法使用时，可以手动复制：

```powershell
python tools/force_raw.py --port <PORT>
mpremote connect <PORT> fs cp certs/ca-bundle.pem :ca.pem
mpremote connect <PORT> reset
```

## 自定义 CA

私有 CA、企业内网或自建 API 仍需单独安装对应根证书。完整部署会用公共 bundle 覆盖 `/ca.pem`，所以私有 CA 请在部署后重新安装。可以：

- 串口执行 `/ca` 并粘贴 PEM；
- 在 Web 设置页展开“高级：自定义 CA”上传；
- 或把证书合并/替换为设备上的 `/ca.pem`。

全量 bundle 会信任 Mozilla 收录的所有公共根 CA，兼容性更好，但信任面比只安装所需根证书更大。全量 bundle 只解决公共根证书信任；设备仍需正确的 RTC/NTP 时间，否则会报证书尚未生效或已过期。更新 bundle 后建议执行 `/test` 和 `/test api`，确认 Wi-Fi、时间和 TLS 均正常。
