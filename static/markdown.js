'use strict';
// 同梱したパーサーを本文表示・プレビュー・一覧の抜粋で共通利用する。
const articleMarkdownParser=markdownit({html:false,linkify:true,typographer:false,breaks:false});
const originalLinkOpen=articleMarkdownParser.renderer.rules.link_open||((tokens,index,options,env,self)=>self.renderToken(tokens,index,options));
articleMarkdownParser.renderer.rules.link_open=(tokens,index,options,env,self)=>{
  const token=tokens[index];
  if (/^(https?:)?\/\//i.test(token.attrGet('href')||'')) {
    token.attrSet('target','_blank');
    token.attrSet('rel','noopener noreferrer');
  }
  return originalLinkOpen(tokens,index,options,env,self);
};
const originalImage=articleMarkdownParser.renderer.rules.image;
articleMarkdownParser.renderer.rules.image=(tokens,index,options,env,self)=>{
  tokens[index].attrSet('loading','lazy');
  tokens[index].attrSet('decoding','async');
  return originalImage(tokens,index,options,env,self);
};
articleMarkdownParser.renderer.rules.table_open=()=>'<div class="markdown-table-wrap"><table>\n';
articleMarkdownParser.renderer.rules.table_close=()=>'</table></div>\n';
function articleMarkdown(source){return articleMarkdownParser.render(String(source||''));}
function markdownPlainText(source){
  const parts=[];
  function collect(tokens){for(const token of tokens||[]){
    if(token.type==='text'||token.type==='code_inline'||token.type==='code_block'||token.type==='fence')parts.push(token.content);
    else if(token.type==='image')parts.push(token.content);
    else if(token.children)collect(token.children);
    else if(token.type==='softbreak'||token.type==='hardbreak')parts.push(' ');
  }}
  for(const token of articleMarkdownParser.parse(String(source||''),{})){
    collect([token]);
    if(token.block)parts.push(' ');
  }
  return parts.join('').replace(/\s+/g,' ').trim();
}

// 記事と研究紹介で共通のMarkdown入力操作。
function insertMarkdownFormat(input,format,onChange=()=>{}){
  if(!input)return;
  const start=input.selectionStart,end=input.selectionEnd;
  const selected=input.value.slice(start,end);
  let replacement,selectionStart,selectionEnd;
  if(format==='image'||format==='table'){
    replacement=format==='image'?'!['+(selected||'画像の説明')+'](https://example.com/image.png)':'\n| 項目 | 内容 |\n| --- | --- |\n| 項目名 | 内容 |\n';
    input.setRangeText(replacement,start,end,'end');
    selectionStart=format==='image'?start+replacement.indexOf('https://'):start+1;
    selectionEnd=format==='image'?start+replacement.length-1:start+replacement.length;
  }else if(format==='heading'||format==='list'){
    const prefix=format==='heading'?'## ':'- ';
    const lineStart=input.value.lastIndexOf('\n',start-1)+1;
    const text=input.value.slice(lineStart,end)||'項目';
    replacement=text.split('\n').map(line=>prefix+line).join('\n');
    input.setRangeText(replacement,lineStart,end,'end');
    selectionStart=lineStart+prefix.length;
    selectionEnd=lineStart+replacement.length;
  }else{
    const text=selected||(format==='link'?'表示する文字':format==='code'?'コード':'強調する言葉');
    if(format==='bold')replacement='**'+text+'**';
    else if(format==='italic')replacement='_'+text+'_';
    else if(format==='link')replacement='['+text+'](https://example.com)';
    else if(format==='code')replacement='\n```text\n'+text+'\n```\n';
    else return;
    input.setRangeText(replacement,start,end,'end');
    if(format==='link'){
      selectionStart=start+text.length+3;
      selectionEnd=selectionStart+'https://example.com'.length;
    }else{
      selectionStart=start+(format==='bold'?2:format==='code'?9:1);
      selectionEnd=selectionStart+text.length;
    }
  }
  input.focus();input.setSelectionRange(selectionStart,selectionEnd);
  onChange();
}
