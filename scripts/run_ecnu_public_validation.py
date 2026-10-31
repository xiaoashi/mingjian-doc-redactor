"""Real API checks. Read credential through a hidden prompt; never save it."""
import argparse
import getpass
import json
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

from mingjian.ai.ecnu import AIConfig, EcnuClient
from mingjian.domain.models import MingjianError, now
from mingjian.engines.rules import scan
from mingjian.domain.models import default_config
from mingjian.parsers.documents import parse_file
from mingjian.services.project import file_hash


def comparable(value):
    return "".join(unicodedata.normalize("NFKC",value).upper().split())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    key=getpass.getpass("本次 API 密钥（隐藏输入，不保存）：")
    config=AIConfig(api_key=key)
    provider=EcnuClient(config)
    args.output.mkdir(parents=True,exist_ok=True)
    records=[]
    try:
        probe=provider.chat('只返回一个JSON对象。', '返回 {"ok":true}，不添加其他内容。',max_tokens=128)
        if probe.get("ok") is not True:
            raise MingjianError("接口探测返回内容不符合预期")
        print("API 认证与 JSON 响应探测通过；等待公开样本任务清单。",flush=True)
        start=time.monotonic()
        while not args.manifest.is_file():
            if time.monotonic()-start>900:
                raise MingjianError("等待样本清单超时，未执行批量请求")
            time.sleep(.5)
        manifest=json.loads(args.manifest.read_text(encoding="utf-8"))
        tests=manifest["tests"]
        if len(tests)>10:
            raise MingjianError("本轮真实请求最多10份样本")
        for index,item in enumerate(tests,1):
            path=Path(item["path"])
            beginning=time.perf_counter()
            record={"id":item["id"],"file":path.name,"source_kind":item["source_kind"],"mode":item["mode"],
                    "source_sha256":file_hash(path),"started_at":now(),"model":config.model}
            print(f"[{index}/{len(tests)}] {path.name} · {item['mode']}",flush=True)
            try:
                if item["mode"]=="vision":
                    parsed=provider.parse_file(path)
                    text="\n".join(b.text for b in parsed.blocks)
                    findings,errors,_=scan(parsed.blocks,default_config(),item["id"])
                    record.update(status="completed",characters=len(text),blocks=len(parsed.blocks),warnings=parsed.warnings,
                                  categories=[f.category for f in findings],rule_errors=errors,
                                  checks=[{"expected":expected,"found":comparable(expected) in comparable(text)} for expected in item.get("expected_values",[])])
                    output={"source":path.name,"blocks":[asdict(b) for b in parsed.blocks],"warnings":parsed.warnings,
                            "metadata":parsed.metadata,"findings":[asdict(f) for f in findings]}
                else:
                    parsed=parse_file(path)
                    suggestions=provider.suggest(parsed.blocks)
                    record.update(status="completed",characters=sum(len(b.text) for b in parsed.blocks),suggestions=len(suggestions),
                                  rejected_suggestions=provider.rejected_suggestions)
                    output={"source":path.name,"blocks":[asdict(b) for b in parsed.blocks],"suggestions":[asdict(s) for s in suggestions]}
                record["seconds"]=time.perf_counter()-beginning
                record["usage_last_request"]=provider.last_usage.copy()
                (args.output/(item["id"]+".json")).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
                print(f"  完成，{record['seconds']:.2f}s；结果待人工核对。",flush=True)
            except Exception as exc:
                record.update(status="failed",seconds=time.perf_counter()-beginning,
                              error=str(exc) if isinstance(exc,MingjianError) else type(exc).__name__)
                print("  未完成："+record["error"],flush=True)
            records.append(record)
            report={"executed_at":now(),"model":config.model,"base_url":config.base_url,"credential_saved":False,
                    "total_usage":provider.usage_total,"records":records,
                    "scope":"公开样本与合成对照的实际调用；不等于独立真值验收，不保证手写全文准确或所有隐私均被发现"}
            (args.output/"实际调用记录.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print("真实请求结束，密钥未写入任何测试记录或源码。",flush=True)
    except Exception as exc:
        message=str(exc) if isinstance(exc,MingjianError) else type(exc).__name__
        (args.output/"探测失败.json").write_text(json.dumps({"executed_at":now(),"error":message,"credential_saved":False},ensure_ascii=False),encoding="utf-8")
        print("API 检查未完成："+message,flush=True)
        return 1
    finally:
        provider.close()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
