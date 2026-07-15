# encoding:utf-8
# 根据您选择的AK已为您生成调用代码
# 检测您当前的AK设置了sn检验，本示例中已为您生成sn计算代码
# encoding:utf-8
# python版本为3.6.2
import requests
import urllib
import hashlib
import time
import json
import os

# 服务地址
host = "https://api.map.baidu.com"

# 接口地址
uri = "/direction/v2/driving"

# 此处填写你在控制台-应用管理-创建应用后获取的AK
ak = "8xLEtsdbow5oHCPBDWLP5OBgbo61CCst"

# 此处填写你在控制台-应用管理-创建应用时，校验方式选择sn校验后生成的SK
sk = "6IASi6Zx1bSRvytGKnHZpxmVpA60FPoN"
timestamp = str(int(time.time()))
# 设置您的请求参数
params = {
    "origin":    "40.01116,116.339303",
    "destination":    "39.936404,116.452562",
    "ak":       ak,
    "timestamp": timestamp,
}

# 拼接请求字符串
paramsArr = []
for key in params:
    paramsArr.append(key + "=" + params[key])

queryStr = uri + "?" + "&".join(paramsArr)

# 对queryStr进行转码，safe内的保留字符不转换
encodedStr = urllib.request.quote(queryStr, safe="/:=&?#+!$,;'@()*[]")

# 在最后直接追加上您的SK
rawStr = encodedStr + sk

# 计算sn
sn = hashlib.md5(urllib.parse.quote_plus(rawStr).encode("utf8")).hexdigest()

# 将sn参数添加到请求中
queryStr = queryStr + "&sn=" + sn

# 请注意，此处打印的url为非urlencode后的请求串
# 如果将该请求串直接粘贴到浏览器中发起请求，由于浏览器会自动进行urlencode，会导致返回sn校验失败
url = host + queryStr
response = requests.get(url)
if response:
    result = response.json()
    
    # 创建输出目录
    output_dir = "/home/bbdwz/projects/website/data/map/output"
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存JSON到文件
    output_file = os.path.join(output_dir, f"route_test.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Response saved to {output_file}")
