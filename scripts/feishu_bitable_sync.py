#!/usr/bin/env python3
"""Sync records from two Feishu Bitable tables into one target table.

Usage:
  export FEISHU_APP_ID=cli_xxx
  export FEISHU_APP_SECRET=xxx
  python scripts/feishu_bitable_sync.py \
    --source app_token_1:table_id_1 \
    --source app_token_2:table_id_2 \
    --target app_token_target:table_id_target \
    --key-field 来源记录ID \
    --source-name-field 来源数据表

Notes:
- This script does an upsert by key field in target table.
- Source records are flattened into target fields and enriched with:
  - 来源记录ID: "{source_table}:{record_id}"
  - 来源数据表: source table id
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import requests

API_BASE = "https://open.feishu.cn/open-apis"


class FeishuAPIError(RuntimeError):
    pass


@dataclass
class TableRef:
    app_token: str
    table_id: str

    @classmethod
    def parse(cls, value: str) -> "TableRef":
        if ":" not in value:
            raise argparse.ArgumentTypeError(
                f"Invalid table ref '{value}', expected format app_token:table_id"
            )
        app_token, table_id = value.split(":", 1)
        return cls(app_token=app_token, table_id=table_id)


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, timeout: int = 20):
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout
        self._token = None
        self._token_expire_at = 0.0

    def _get_tenant_token(self) -> str:
        if self._token and time.time() < self._token_expire_at - 60:
            return self._token

        url = f"{API_BASE}/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise FeishuAPIError(
                f"Get tenant token failed: code={payload.get('code')} msg={payload.get('msg')}"
            )

        self._token = payload["tenant_access_token"]
        self._token_expire_at = time.time() + int(payload.get("expire", 7200))
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> dict:
        token = self._get_tenant_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        url = f"{API_BASE}{path}"
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise FeishuAPIError(
                f"API {method} {path} failed: code={payload.get('code')} msg={payload.get('msg')}"
            )
        return payload

    def list_records(self, table: TableRef, page_size: int = 200) -> Iterable[dict]:
        page_token = None
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token

            payload = self._request(
                "GET",
                f"/bitable/v1/apps/{table.app_token}/tables/{table.table_id}/records",
                params=params,
            )
            data = payload.get("data", {})
            for item in data.get("items", []):
                yield item

            if not data.get("has_more"):
                break
            page_token = data.get("page_token")

    def batch_create_records(self, table: TableRef, records: List[dict]) -> None:
        if not records:
            return
        self._request(
            "POST",
            f"/bitable/v1/apps/{table.app_token}/tables/{table.table_id}/records/batch_create",
            json={"records": [{"fields": r} for r in records]},
        )

    def batch_update_records(self, table: TableRef, records: List[Tuple[str, dict]]) -> None:
        if not records:
            return
        self._request(
            "POST",
            f"/bitable/v1/apps/{table.app_token}/tables/{table.table_id}/records/batch_update",
            json={"records": [{"record_id": rid, "fields": f} for rid, f in records]},
        )


def chunked(items: List, size: int) -> Iterable[List]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def build_target_fields(
    source_table: TableRef,
    source_record: dict,
    key_field: str,
    source_name_field: str,
) -> dict:
    fields = dict(source_record.get("fields", {}))
    fields[key_field] = f"{source_table.table_id}:{source_record['record_id']}"
    fields[source_name_field] = source_table.table_id
    return fields


def sync_sources_to_target(
    client: FeishuClient,
    sources: List[TableRef],
    target: TableRef,
    key_field: str,
    source_name_field: str,
    batch_size: int,
) -> Tuple[int, int]:
    existing_by_key: Dict[str, str] = {}
    for rec in client.list_records(target):
        key = rec.get("fields", {}).get(key_field)
        if key:
            existing_by_key[str(key)] = rec["record_id"]

    to_create: List[dict] = []
    to_update: List[Tuple[str, dict]] = []

    for source in sources:
        for src_record in client.list_records(source):
            fields = build_target_fields(source, src_record, key_field, source_name_field)
            key = fields[key_field]
            if key in existing_by_key:
                to_update.append((existing_by_key[key], fields))
            else:
                to_create.append(fields)

    for batch in chunked(to_create, batch_size):
        client.batch_create_records(target, batch)

    for batch in chunked(to_update, batch_size):
        client.batch_update_records(target, batch)

    return len(to_create), len(to_update)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync two+ Feishu tables into one target table")
    parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        type=TableRef.parse,
        required=True,
        help="Source in format app_token:table_id, repeat this argument for multiple sources",
    )
    parser.add_argument(
        "--target",
        type=TableRef.parse,
        required=True,
        help="Target in format app_token:table_id",
    )
    parser.add_argument(
        "--key-field",
        default="来源记录ID",
        help="Unique key field name in target table",
    )
    parser.add_argument(
        "--source-name-field",
        default="来源数据表",
        help="Field name in target table to mark source table",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("Missing FEISHU_APP_ID or FEISHU_APP_SECRET", file=sys.stderr)
        return 2

    client = FeishuClient(app_id=app_id, app_secret=app_secret)

    created, updated = sync_sources_to_target(
        client=client,
        sources=args.sources,
        target=args.target,
        key_field=args.key_field,
        source_name_field=args.source_name_field,
        batch_size=args.batch_size,
    )
    print(f"Sync done. created={created}, updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
