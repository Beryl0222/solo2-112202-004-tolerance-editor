# 技术量词限定与工装规格签发

业务资料使用虚构编号和合成叙述。文本保持来件拼写和措辞。各表是独立的来源记录。
TSV 文件采用 UTF-8，首行为列名。行首 id 是该表的稳定记录号，空格和标点属于原文，空单元格表示来件未填。
日期时间采用所在地本地时间。资料内注明为编号列表的字段用竖线分隔。

## 资料字段

- `materials/specs.tsv`。lang 是正文语言，amends 表示补充函所指来文号，issued 是出具日。role 记录材料身份。
- `materials/gauges.tsv`。nominal 和 reading 与 uncertainty 共用 unit。uncertainty 是记录给出的对称半区间，空值表示没有提供。stage 为计量单来源写法。
- `materials/authorizations.tsv`。use 为实际投用的原始记录状态。serial 对应制造编号，station 是具体申请工位。
- `materials/measure_terms.tsv`。value 是原有计量术语登记。source 是登记术语适用的文书号，极限以文书原句为准。

## 运行资料检查

使用 CPython 3.11.2 和标准库。资料检查仅读取输入。

```sh
python3 pack_check.py
python3 -m unittest -v
```
