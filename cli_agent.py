# Complete CLI Agent v1.0
# (See conversation for design rationale)

import logging
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

MODEL_NAME = "gemma4:e4b"
ALLOWED_COMMANDS: Set[str] = {"ls","pwd","cat","grep","touch","echo","curl"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

class CLIAgent:
    def __init__(self):
        self.llm = ChatOllama(model=MODEL_NAME, temperature=0, num_ctx=16384)
        self.chat = [SystemMessage(content=self.system_prompt())]

    def system_prompt(self):
        return """あなたはLinuxコマンド実行エージェントです。

【利用可能コマンド】
ls, pwd, cat, grep, touch, echo, curl

【出力フォーマット】
コマンドを実行する場合は、必ず以下の形式【のみ】を出力してください。余計な解説や挨拶は一切不要です。
EXECUTE: <command>

例:
EXECUTE: ls Daily

【厳格な制約事項】
1. 実行結果はシステムから渡されます。実際にコマンドを実行して得られた結果のみを信じてください。
2. 実行していない情報を推測して回答してはいけません。
3. ファイル内容やWebページ内容を絶対に捏造しないでください。
4. 目的を達成するために結果が不足している場合は、思考を止めずに追加のコマンドを要求してください。"""

    def read_notes(self, folder:str)->List[Dict]:
        p=Path(folder)
        if not p.exists():
            return []
        out=[]
        for f in p.glob("*.md"):
            try:
                out.append({"name":f.name,"content":f.read_text(encoding="utf-8")})
            except Exception as e:
                log.exception(e)
        return out

    def extract_urls(self,text):
        return re.findall(r"https?://\S+",text)

    def execute(self, cmd):
        args = shlex.split(cmd)
        if not args:
            return ""
        if args[0] not in ALLOWED_COMMANDS:
            return "Denied"
        if args[0] == "curl":
            # -sSL に加え、一般的なブラウザの User-Agent を偽装するヘッダーを追加
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            args.insert(1, "-sSL")
            args.insert(2, "-H")
            args.insert(3, f"User-Agent: {user_agent}")
        
        # text=True を外し、バイナリ(bytes)として出力を取得する
        r = subprocess.run(args, capture_output=True, text=False, timeout=20)
    
        # 終了ステータスに応じて標準出力か標準エラー出力を選択
        raw_output = r.stdout if r.returncode == 0 else r.stderr
    
        # バイト列を適切な文字コードにデコードする
        try:
            return raw_output.decode('utf-8')
        except UnicodeDecodeError:
            try:
                # ITmedia等のShift_JIS(CP932)のサイト対策
                return raw_output.decode('cp932')
            except UnicodeDecodeError:
                # どちらでもデコードできない場合の最終手段（エラーにせず置き換える）
                return raw_output.decode('utf-8', errors='replace')

    def workflow_literature(self):
        Path("LiteratureNote").mkdir(exist_ok=True)
        for note in self.read_notes("Daily"):
            for i,url in enumerate(self.extract_urls(note["content"]),1):
                # 先に出力先のパスを確定させる
                out = Path("LiteratureNote") / f"{Path(note['name']).stem}_{i}.md"
                
                # すでにファイルが存在する場合はスキップ（追加）
                if out.exists():
                    print(f"Skipping (Already exists): {out.name}")
                    continue
                
                # 存在しない場合のみ、FetchとLLM要約を実行
                print("Fetching:",url)
                fetch_url = url
                if "zenn.dev" in url and "/articles/" in url:
                    article_id = url.split("/")[-1]
                    fetch_url = f"https://zenn.dev/api/articles/{article_id}"
                
                html = self.execute(f'curl "{fetch_url}"')
                
                # 1. <script> と <style> タグを中身ごと完全に削除
                clean_html = re.sub(r'<script\b[^>]*>([\s\S]*?)</script>', '', html)
                clean_html = re.sub(r'<style\b[^>]*>([\s\S]*?)</style>', '', clean_html)
                
                # 2. 【大幅強化】記事本文が始まりそうなHTML要素の目印で、前方のノイズ（ログイン等）をカット
                for marker in ['<article', '<main', 'class="article', 'id="main"']:
                    if marker in clean_html:
                        clean_html = clean_html.split(marker, 1)[-1]
                        break
                
                # 3. 【最重要】すべてのHTMLタグ（<...>）を完全に消去して「純粋なテキスト」にする
                plain_text = re.sub(r'<[^>]*>', '', clean_html)
                
                # 4. 無駄な連続改行や空白をスッキリまとめる（LLMが読みやすくなる）
                plain_text = re.sub(r'\n\s*\n', '\n', plain_text).strip()
                
                # デバッグ表示用（本当に本文が入っているか確認するため）
                print("\n" + "="*40)
                print(f"[DEBUG] 実際にLLMへ渡す純粋な本文テキスト（先頭500文字）:")
                print(plain_text[:500])
                print("="*40 + "\n")
                
                prompt = f"""以下の記事を日本語で、Markdownで簡潔に要約してください。

URL:{url}
本文:
{plain_text[:7000]}

【出力時の厳格なルール】
記事内の重要な技術用語、固有名詞、役割、概念（例: [[プロダクトマネージャー]], [[AI]], [[アジャイル開発]], [[プロセス管理]], [[Product Taste]] など）については、後からリンクできるように、必ず `[[用語]]` のように2重の角括弧で囲んで出力してください。普通のテキストのまま出力せず、積極的にWikiLink化してください。"""
                
                summary = self.llm.invoke(prompt).content
                daily_link = Path(note['name']).stem
                out.write_text(f"# Literature Note\n\nContext: [[{daily_link}]]\nSource: {url}\n\n{summary}", encoding="utf-8")
                print("Created:",out)

    def workflow_permanent(self):
        Path("PermanentNote").mkdir(exist_ok=True)
        corpus=""
        for folder in ("FleetingNote","LiteratureNote"):
            for n in self.read_notes(folder):
                corpus+=f"\n\n# {n['name']}\n{n['content']}"
        if not corpus.strip():
            print("No notes found.")
            return
        prompt = f"""あなたは、断片的なメモから永続的な知識（Permanent Note）を構築する専門家です。
提供された以下の複数のメモ（コーパス）を深く分析・統合し、1つまたは複数の独立した「概念ノート」としてMarkdown形式で作成してください。

【作成時の厳格なルール】
1. **1概念＝1ノートの原則**:
   複数のトピックがある場合は、別々のセクション（## 見出し）に分けて、それぞれが独立した「一つの知識のカード」になるようにしてください。

2. **自律性と自分の言葉**:
   単なるコピペや要約ではなく、内容を抽象化・構造化し、後から見返してもそれ単体で理解できる「普遍的な知識」として記述してください。

3. **強力なリンクの網（WikiLink）の構築**:
   - メモ同士のつながりや、重要なキーワード、関連する概念は、必ず `[[関連概念]]` のように2重の角括弧で囲んで積極的にリンク化してください。
   - すでに存在しているであろう技術名（例: `[[Python]]`, `[[LangChain]]`）や、抽象的な概念（例: `[[文字コード対策]]`, `[[疎結合]]`）を積極的にリンクにしてください。

4. **ソースへのバックリンク**:
   各セクション、またはノートの末尾に、どの元ノート（例: `2026-06-09_1.md` や `FleetingNoteのファイル名`）から抽出した知識なのか、`元メモ: [[ファイル名]]` という形で必ずバックリンクを含めてください。

【入力コーパス】
{corpus[:50000]}"""
        result=self.llm.invoke(prompt).content
        name=datetime.now().strftime("permanent_%Y%m%d_%H%M%S.md")
        out=Path("PermanentNote")/name
        out.write_text(result,encoding="utf-8")
        print("Created:",out)

    def workflow_help(self):
        print("""
Available workflows
/workflow:create_literature_note_from_daily
/workflow:draft_permanent_note
/workflow:test
/workflow:help
""")

    def workflow_test(self):
        for d in ("Daily","LiteratureNote","FleetingNote","PermanentNote"):
            p=Path(d)
            print(f"{d}: {'OK' if p.exists() else 'Missing'}")

    def llm_chat(self,msg):
        self.chat.append(HumanMessage(content=msg))
        r=self.llm.invoke(self.chat)
        txt=r.content.strip()
        if txt.startswith("EXECUTE:"):
            cmd=txt[8:].strip()
            res=self.execute(cmd)
            print(res)
            self.chat.append(AIMessage(content=txt))
            self.chat.append(SystemMessage(content=res))
        else:
            print(txt)
            self.chat.append(AIMessage(content=txt))

    def run(self):
        print("CLI Agent v1.0")
        while True:
            s=input("あなた > ").strip()
            if s.lower()=="exit":
                break
            if not s:
                continue
            if s=="/workflow:create_literature_note_from_daily":
                self.workflow_literature(); continue
            if s=="/workflow:draft_permanent_note":
                self.workflow_permanent(); continue
            if s=="/workflow:help":
                self.workflow_help(); continue
            if s=="/workflow:test":
                self.workflow_test(); continue
            self.llm_chat(s)

if __name__=="__main__":
    CLIAgent().run()
