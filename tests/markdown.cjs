const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync('static/vendor/markdown-it/markdown-it.min.js','utf8'),context);
vm.runInContext(fs.readFileSync('static/markdown.js','utf8'),context);
const render=source=>vm.runInContext(`articleMarkdown(${JSON.stringify(source)})`,context);
const plain=source=>vm.runInContext(`markdownPlainText(${JSON.stringify(source)})`,context);
assert.match(render('# 大見出し\n\n## 見出し\n\n### 小見出し'),/<h1>大見出し<\/h1>[\s\S]*<h2>見出し<\/h2>[\s\S]*<h3>小見出し<\/h3>/);
assert.match(render('**太字** と *斜体*、~~取り消し~~、`コード`'),/<strong>太字<\/strong>[\s\S]*<em>斜体<\/em>[\s\S]*<s>取り消し<\/s>[\s\S]*<code>コード<\/code>/);
assert.match(render('- 項目\n  - 子項目\n\n1. 手順\n2. 手順'),/<ul>[\s\S]*<ul>[\s\S]*<ol>[\s\S]*<li>手順/);
assert.match(render('> 引用\n\n---'),/<blockquote>[\s\S]*引用[\s\S]*<hr>/);
assert.match(render('```js\nconst html = "<script>";\n```'),/<pre><code class="language-js">const html = &quot;&lt;script&gt;&quot;;/);
assert.match(render('| 項目 | 数値 |\n| :--- | ---: |\n| A | 2 |'),/<div class="markdown-table-wrap"><table>[\s\S]*<th style="text-align:left">項目[\s\S]*<td style="text-align:right">2/);
assert.match(render('[リンク](https://example.com)'),/<a href="https:\/\/example.com" target="_blank" rel="noopener noreferrer">リンク<\/a>/);
assert.doesNotMatch(render('[記事一覧](#articles)'),/target="_blank"/);
assert.match(render('![装置](/media/article-thumbnails/test.webp)'),/<img src="\/media\/article-thumbnails\/test.webp" alt="装置" loading="lazy" decoding="async">/);
for(const source of ['<script>alert(1)</script>','<img src=x onerror=alert(1)>','[リンク](javascript:alert(1))','[リンク](vbscript:alert(1))','[リンク](data:text/html;base64,PHNjcmlwdD4=)','![説明](javascript:alert(1))']){
 const html=render(source);
 assert.doesNotMatch(html,/<script\b|<img[^>]*\bonerror\s*=|(?:href|src)=["'](?:javascript|vbscript|data:text\/html):/i);
}
assert.equal(plain('## お知らせ\n\n**研究の進捗**を[説明](https://example.com)します。\n\n- 成果1\n- 成果2'),'お知らせ 研究の進捗を説明します。 成果1 成果2');
assert.doesNotMatch(plain('![装置](https://example.com/image.png)\n\n[資料](https://example.com/file)'),/https:/);
console.log('Markdown blocks, formatting, tables, links, images, excerpt extraction and unsafe content: OK');
