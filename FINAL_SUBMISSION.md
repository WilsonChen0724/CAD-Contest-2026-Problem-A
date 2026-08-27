# Final Submission（Team 1070）

官方會在 RedHat 8 主機上，把 submission 複製到不同路徑後，從 submission
根目錄執行：

```bash
./cada1070_final -config <config_file_path>
```

因此提交時，`cada1070_final` 必須直接位於 submission 根目錄，不可只放在
第二層資料夾，也不可提交 Docker image。程式所需套件必須離線自含；評測期間
只有 OpenAI／Anthropic model API 可以連線。

## 產生提交包

在專案根目錄執行：

```bash
python scripts/build_final_submission.py
```

輸出：

- `dist/final_test_submission/`：可直接上傳的目錄內容。
- `dist/cada1070_final_submission.tar.gz`：相同內容的封裝檔；解壓後
  `cada1070_final` 會直接出現在目前目錄。

若從 Windows 產生 submission，請優先上傳 `.tar.gz`，因為 tar 會保留
`cada1070_final` 的 Linux executable bit。

提交包包含 Linux x86_64 CPython 3.11 runtime 與 Linux z3-solver，不依賴
評測機現場安裝 Python 套件，也不包含本機 Windows Yosys、公開 testcase、
測試輸出、文件或 API key。

## 上傳前檢查

在一台 Linux x86_64 主機解壓後執行：

```bash
chmod +x cada1070_final
printf '%s\n' \
  'This is the beginning of testcase smoke.' \
  | ./cada1070_final -config /absolute/path/to/config.yaml
```

stdout 必須只出現完整 response block：

```text
#RESPONSE 1
...
#END 1
```

並應在 executable 的工作目錄生成 `smoke.log`。正式 testcase 的相對輸出
netlist 及 `<case>.log` 也會寫到共同工作目錄，符合 Q&A A60、A70、A71。

也可用專案內的成品 smoke script，一次檢查 launcher、包內 Python/z3、
response tags、log 與輸出 netlist：

```bash
bash scripts/smoke_final_submission.sh \
  dist/cada1070_final_submission.tar.gz \
  config.example.yaml \
  'A_release testcase_0510/testcase/test01/test01.v'
```

請勿把自己的 API key 放進提交包；正式評測會在 `-config` 指定的檔案內提供
官方 key。
