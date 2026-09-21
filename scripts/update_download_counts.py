#!/usr/bin/env python3
"""repo.json の DownloadCount を GitHub Releases の実ダウンロード数で更新する。

Dalamud のプラグインインストーラーは repo.json の DownloadCount をそのまま
表示する（自分では計測しない）。0 のときは非表示になるため、ここで実数を
書き込むことで「by 作者 (1,234 downloads)」と表示されるようになる。

集計方法:
  そのプラグインの「全リリースの合計」。アセット名からバージョン部分を
  取り除いた名前(stem)が一致するものをすべて合算する。
    PrivateReleaseRepo     AutoCollector.zip       → stem "AutoCollector"
    PrivateDevReleaseRepo  AnoMech-0.3.7.4.zip     → stem "AnoMech"

既存の書式(インデント・改行コード・キーの並び)を壊さないよう、
JSON を再シリアライズせず該当箇所だけを置換する。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
# 末尾の "-1.2.3.4" のようなバージョン表記を取り除くための正規表現
VERSION_SUFFIX = re.compile(r"[-_]v?\d+(?:\.\d+)*$")


def api_get(url: str):
    """GitHub API を叩いて JSON を返す。"""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "update-download-counts")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def asset_stem(filename: str) -> str:
    """アセット名からバージョンを除いた識別子を返す。"""
    name = filename[:-4] if filename.lower().endswith(".zip") else filename
    return VERSION_SUFFIX.sub("", name)


def parse_link(url: str):
    """配布URLから (owner, repo, アセット名) を取り出す。"""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/releases/", url or "")
    if not m:
        return None
    return m.group(1), m.group(2), url.rsplit("/", 1)[-1]


def fetch_counts(owner: str, repo: str) -> dict[str, int]:
    """リポジトリの全リリースを走査し、stem ごとの合計DL数を返す。"""
    totals: dict[str, int] = {}
    page = 1
    while True:
        try:
            releases = api_get(
                f"{API}/repos/{owner}/{repo}/releases?per_page=100&page={page}"
            )
        except urllib.error.HTTPError as e:
            print(f"  警告: {owner}/{repo} を取得できません (HTTP {e.code})")
            return totals
        if not releases:
            break
        for rel in releases:
            for asset in rel.get("assets", []):
                stem = asset_stem(asset["name"])
                totals[stem] = totals.get(stem, 0) + asset.get("download_count", 0)
        if len(releases) < 100:
            break
        page += 1
    return totals


def iter_entries(text: str):
    """JSON配列の各要素を (オブジェクト, 開始位置, 終了位置) で返す。

    書式を保つため、パースはするが文字列は組み立て直さない。
    """
    decoder = json.JSONDecoder()
    i = text.index("[") + 1
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] == "]":
            break
        obj, end = decoder.raw_decode(text, i)
        yield obj, i, end
        i = end


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "repo.json")
    if not os.path.isfile(path):
        print(f"repo.json が見つかりません: {path}")
        return 1

    # 改行コードを保つため newline='' で読み書きする
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()

    entries = list(iter_entries(text))
    cache: dict[tuple[str, str], dict[str, int]] = {}
    replacements = []  # (開始, 終了, 置換後テキスト)
    changed = 0

    for obj, start, end in entries:
        name = obj.get("InternalName", "?")
        link = parse_link(obj.get("DownloadLinkInstall", ""))
        if not link:
            print(f"  スキップ: {name} (配布URLを解釈できません)")
            continue
        owner, repo, filename = link

        if (owner, repo) not in cache:
            cache[(owner, repo)] = fetch_counts(owner, repo)
        stem = asset_stem(filename)
        count = cache[(owner, repo)].get(stem)
        if count is None:
            print(f"  スキップ: {name} (リリース資産 {stem}.zip が見つかりません)")
            continue

        block = text[start:end]
        new_block, n = re.subn(
            r'("DownloadCount"\s*:\s*)\d+', rf"\g<1>{count}", block, count=1
        )
        if n == 0:
            print(f"  スキップ: {name} (DownloadCount フィールドがありません)")
            continue

        old = obj.get("DownloadCount", 0)
        mark = "" if old == count else f"  (旧 {old})"
        print(f"  {name:22} {count:>6}{mark}")
        if new_block != block:
            replacements.append((start, end, new_block))
            changed += 1

    if not replacements:
        print("変更はありません")
        return 0

    # 後ろから置換して位置ずれを防ぐ
    for start, end, new_block in sorted(replacements, reverse=True):
        text = text[:start] + new_block + text[end:]

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"{changed} 件のプラグインを更新しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
