#!/usr/bin/env python3
import os
from mijiaAPI.login import mijiaLogin, LoginError  # 就用你贴的这个类

AUTH_PATH = os.path.expanduser("~/.config/mijia-api/mijia-api-auth.json")

def main():
    login = mijiaLogin(save_path=AUTH_PATH)
    try:
        auth = login.QRlogin()
        print("登录成功！新的 token 已写入:", AUTH_PATH)
        print("userId:", auth.get("userId"))
        print("expireTime:", auth.get("expireTime"))
    except LoginError as e:
        print("登录失败:", e)

if __name__ == "__main__":
    main()
