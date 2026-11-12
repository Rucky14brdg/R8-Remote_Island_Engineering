'use strict';
const issueStates=['未整理','相談受付','調査・検討中','取組中','解決・一区切り'];
const issueColors=['#84928e','#b87932','#557e99','#377f70','#7775a0'];
const issueCategories=['暮らし・福祉','交通・物流','防災','環境・エネルギー','産業・観光','教育・文化','情報・通信','その他'];
let issueItems=[], issueView='list', issueQuery='', issueCategory='', issueRole='', issueOnlyUnlinked=false, issueMap=null;
let issuePageToken=0;
let issueMapSelectedPlaceId='', issueMapPlaces=[], issueMapFilteredItems=[], issueMapMarkers=new Map();
const issueHref=id=>'#issue/'+encodeURIComponent(id);
function destroyIssueMap(){if(issueMap){issueMap.remove();issueMap=null;}issueMapMarkers.clear();}
function issueLocations(i){return (i.targets||[]).filter(t=>Number.isFinite(t.latitude)&&Number.isFinite(t.longitude));}
function issuePosition(i){return issueLocations(i).length>0;}
function issueLocationMeaning(){return '関連する地域・現場の位置';}
function placeFromEntity(e){return {id:e.id,title:title(e),latitude:literal(e,'緯度')===''?null:Number(literal(e,'緯度')),longitude:literal(e,'経度')===''?null:Number(literal(e,'経度')),location_precision:literal(e,'位置の精度')||'おおよその場所'};}
function issueStatus(i){return issueStates.includes(i.status)?i.status:'未整理';}
function issueBadge(i){const s=issueStatus(i);return `<span class="issue-status" style="--status-color:${issueColors[issueStates.indexOf(s)]}">${esc(s)}</span>`;}
function issueCard(i){return `<article class="panel issue-card"><div class="card-topline">${issueBadge(i)}<span class="fine">${esc(i.category||'分野未設定')}</span></div><a class="issue-title" href="${issueHref(i.id)}">${esc(i.title)}</a><p>${esc(i.description||'詳しい内容はまだ記入されていません。')}</p><div class="issue-meta">${icon('pin')} ${esc(i.targets.map(t=>t.title).join('・')||'場所未設定')}</div><div class="issue-card-footer"><span>${esc(i.reporter_role||'投稿者の立場未設定')}</span><span>${i.research.length?`研究・活動 ${i.research.length}件`:'研究・活動との接続を募集中'}</span></div></article>`;}
function issueOptions(values,selected,emptyLabel){return `${emptyLabel?`<option value="">${esc(emptyLabel)}</option>`:''}${values.map(v=>`<option value="${esc(v)}" ${v===selected?'selected':''}>${esc(v)}</option>`).join('')}`;}
async function fetchIssues(){const response=await fetch('/api/issues');if(!response.ok)throw new Error('課題を読み込めませんでした');return (await response.json()).items;}
function communityNote(i,index=0){
  const status={'未整理':'状況の確認待ち','相談受付':'相談受付中','調査・検討中':'調査・検討中','取組中':'取り組み中','解決・一区切り':'ひと区切り'}[issueStatus(i)];
  const colors=['yellow','blue','pink','green'];
  return `<article class="community-note note-${colors[index%colors.length]}"><div class="community-note-top"><span>${icon('pin')}${esc(i.targets.map(mapPlaceTitle).join('・')||'場所はこれから')}</span><span class="community-note-number">${String(index+1).padStart(2,'0')}</span></div><h3><a href="${issueHref(i.id)}">${esc(i.title)}</a></h3><p class="community-note-description">${esc(i.description||'詳しい内容は、これから書き足せます。')}</p><div class="community-note-tags">${i.category?`<span>${esc(i.category)}</span>`:''}<span>${esc(status)}</span></div><div class="community-note-research">${icon('layers')}<span>${i.research.length?`関連する研究・活動 ${i.research.length}件`:'研究・活動とのつながりを募集中'}</span></div><div class="community-note-bottom"><span>${esc(i.reporter_name||i.reporter_role||'')}${i.reporter_name&&i.reporter_role?` · ${esc(i.reporter_role)}`:''}</span><a href="${issueHref(i.id)}" aria-label="${esc(i.title)}を詳しく見る">詳しく見る <span aria-hidden="true">→</span></a></div></article>`;
}
async function renderIssues(view=''){
  issueView=['list','board','map'].includes(view)?view:'list';
  const token=++issuePageToken;destroyIssueMap();
  setPage('地域のお困りごと','<div class="page issues-page"><div class="loading">困りごとを読み込んでいます…</div></div>','issues');
  try{
    const items=await fetchIssues();if(token!==issuePageToken||!location.hash.startsWith('#issues'))return;issueItems=items;
    setPage('地域のお困りごと',`<div class="page issues-page community-page"><div class="page-heading"><div><div class="eyebrow">COMMUNITY</div><h1>地域のお困りごと</h1><p>日々の「困った」を持ち寄って、研究や実践につなげる掲示板。</p></div><a class="button primary" href="#issue-new">${icon('plus')}困りごとを書く</a></div><div class="community-controls"><div class="issue-view-switch" aria-label="表示方法">${[['list','掲示板'],['map','地図'],['board','進み具合']].map(([v,label])=>`<button class="button small" data-issue-view="${v}" aria-pressed="${issueView===v}">${v==='list'?icon('file'):v==='map'?icon('pin'):icon('layers')}${label}</button>`).join('')}</div><a class="quiet-link" href="#place-new">地域・現場を登録 ${icon('plus')}</a></div><div class="issue-toolbar community-search"><div class="searchbar">${icon('search')}<input id="issue-search" type="search" aria-label="課題を検索" placeholder="困りごと・地域・施設の名前で検索" value="${esc(issueQuery)}"></div><select id="issue-category-filter" aria-label="分野で絞り込む">${issueOptions(issueCategories,issueCategory,'すべての分野')}</select><select id="issue-role-filter" aria-label="投稿者の立場で絞り込む">${issueOptions(['住民','行政','学生','教員','地域団体・事業者','その他'],issueRole,'すべての立場')}</select></div><div class="community-filter-line"><label class="issue-check"><input type="checkbox" id="issue-unlinked" ${issueOnlyUnlinked?'checked':''}>研究・活動がまだ紐づいていない困りごと</label><span id="issue-result-count" role="status"></span></div><div id="issue-results"></div><p class="community-page-help">解き方が分からなくても大丈夫。身近な気づきから書いてみてください。</p></div>`,'issues');
    $('topbar-actions').innerHTML='<a class="button small" href="#issue-new">'+icon('plus')+'困りごとを書く</a>';
    drawIssueResults();
  }catch(error){if(token===issuePageToken)setPage('地域のお困りごと',`<div class="page"><p role="alert">${esc(error.message)}</p><button class="button" data-issue-retry>再読み込み</button></div>`,'issues');}
}
function filteredIssues(){const q=issueQuery.trim().toLocaleLowerCase();return issueItems.filter(i=>(!issueCategory||i.category===issueCategory)&&(!issueRole||i.reporter_role===issueRole)&&(!issueOnlyUnlinked||!i.research.length)&&[i.title,i.description,i.background,i.desired_support,i.category,...i.targets.map(t=>t.title)].join(' ').toLocaleLowerCase().includes(q));}
function mapPlaceTitle(place){const name=String(place.title||'').trim();return !name||name===place.id&&/^target_[a-f0-9]+$/i.test(name)?'指定した地点':name;}
function mapPlacesForIssues(items){
  const places=new Map();
  for(const e of data.entities.filter(e=>e.types.includes('Target'))){const place=placeFromEntity(e);if(Number.isFinite(place.latitude)&&Number.isFinite(place.longitude))places.set(place.id,place);}
  for(const i of items)for(const place of issueLocations(i))places.set(place.id,place);
  return [...places.values()].sort((a,b)=>mapPlaceTitle(a).localeCompare(mapPlaceTitle(b),'ja')||a.id.localeCompare(b.id));
}
function selectIssueMapPlace(id){
  const place=issueMapPlaces.find(p=>p.id===id);issueMapSelectedPlaceId=place?.id||'';
  const panel=$('issue-map-selection');if(!panel)return;
  for(const [placeId,marker] of issueMapMarkers){const element=marker.getElement?.();element?.classList.toggle('is-selected',placeId===issueMapSelectedPlaceId);element?.setAttribute('aria-pressed',String(placeId===issueMapSelectedPlaceId));}
  if(!place){panel.innerHTML='';issueMap?.closePopup();return;}
  const related=issueMapFilteredItems.filter(i=>i.targets.some(t=>t.id===place.id));
  panel.innerHTML=related.length?`<div class="issue-map-list stack">${related.map(i=>communityNote(i,Math.max(0,issueItems.findIndex(item=>item.id===i.id)))).join('')}</div>`:'';
  issueMapMarkers.get(place.id)?.openPopup();
}
function drawIssueResults(){
  destroyIssueMap();const items=filteredIssues();
  if($('issue-result-count'))$('issue-result-count').textContent=`${items.length}件の困りごと${issueView==='map'?` · 地図に表示 ${items.filter(issuePosition).length}件`:''}`;
  if(issueView==='map'){
    issueMapFilteredItems=items;issueMapPlaces=mapPlacesForIssues(issueItems);
    $('issue-results').innerHTML=`<div class="issue-map-layout community-map-layout"><div class="community-map-frame"><div id="issues-map" class="issue-map" aria-label="地域課題の地図"></div><p id="issue-map-message" class="hint">ピンを押すと場所名と困りごとを表示します。地図の空白を押すと閉じます。</p></div><aside class="community-map-sidebar" aria-label="困りごとの付箋"><div id="issue-map-selection"></div></aside></div>`;
    createIssueMap('issues-map',items,null,issueMapPlaces,{onSelect:place=>selectIssueMapPlace(place.id),onClear:()=>selectIssueMapPlace('')});
    selectIssueMapPlace(issueMapSelectedPlaceId);
    if(!issueMapPlaces.length)$('issue-map-message').textContent='位置を登録した地域・現場はまだありません。困りごとの掲示板からも探せます。';
    return;
  }
  $('issue-results').innerHTML=!items.length?`<div class="empty community-empty"><h2>${issueItems.length?'条件に合う困りごとがありません':'最初の困りごとを、ここに。'}</h2><p>${issueItems.length?'キーワードや絞り込みを変えてみてください。':'誰が、どこで、何に困っているか。身近な言葉で書けます。'}</p><a class="button primary" href="#issue-new">困りごとを書く</a></div>`:issueView==='list'?`<section class="community-chalkboard" aria-label="困りごとの掲示板"><div class="community-board-caption"><h2>みんなの困りごと</h2><span>小さな気づきも、ここへ。</span></div><div class="community-note-grid">${items.map(communityNote).join('')}</div><div class="community-board-tray" aria-hidden="true"><span></span><span></span><i></i></div></section>`:`<div class="issue-board">${issueStates.map((state,index)=>{const column=items.filter(i=>issueStatus(i)===state);return `<section class="issue-column" style="--status-color:${issueColors[index]}"><h2><span>${esc(state)}</span><span>${column.length}</span></h2><div class="stack">${column.map(issueCard).join('')||'<p class="issue-column-empty">この状態の困りごとはありません</p>'}</div></section>`;}).join('')}</div>`;
}
function createIssueMap(id,items,onClick,places=[],options={}){
  if(!window.L){$(id).innerHTML='<p class="form-error">地図を読み込めませんでした。地域・現場の緯度・経度は入力欄から指定できます。</p>';return;}
  issueMap=L.map(id,{scrollWheelZoom:false}).setView([34,133],6);
  const tile=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'}).addTo(issueMap);
  tile.on('tileerror',()=>{const m=$('issue-map-message');if(m)m.textContent='背景地図を読み込めません。地域・現場のピンと課題は表示できます。接続を確認してください。';});
  const locations=new Map();
  for(const place of places){if(Number.isFinite(place.latitude)&&Number.isFinite(place.longitude))locations.set(place.id,{place,issues:[]});}
  for(const issue of items){for(const place of issueLocations(issue)){if(!locations.has(place.id))locations.set(place.id,{place,issues:[]});const group=locations.get(place.id);if(!group.issues.some(i=>i.id===issue.id))group.issues.push(issue);}}
  const points=[];
  for(const {place,issues} of locations.values()){
    const point=[place.latitude,place.longitude];points.push(point);
    const label=mapPlaceTitle(place);
    const content=`<a class="place-popup-link" href="#entity/${encodeURIComponent(place.id)}">${esc(label)}</a>`;
    const marker=L.marker(point,{title:`${label}：${issues.length}件の課題`,bubblingMouseEvents:false,icon:L.divIcon({className:'place-marker',html:`<span class="place-pin"><span>${issues.length||'●'}</span></span>`,iconSize:[34,42],iconAnchor:[17,42],popupAnchor:[0,-38]})}).addTo(issueMap).bindPopup(content);
    issueMapMarkers.set(place.id,marker);
    if(options.onSelect){marker.on('click',()=>options.onSelect(place));marker.getElement()?.setAttribute('aria-pressed','false');}
  }
  if(points.length===1)issueMap.setView(points[0],16);else if(points.length>1)issueMap.fitBounds(L.latLngBounds(points).pad(.3),{maxZoom:16});
  if(onClick)issueMap.on('click',onClick);
  if(options.onClear)issueMap.on('click',options.onClear);
}
async function getIssue(id){return (await fetchIssues()).find(i=>i.id===id);}
async function renderIssue(id){const token=++issuePageToken;destroyIssueMap();setPage('課題の詳細','<div class="page loading">課題を読み込んでいます…</div>','issues');try{const i=await getIssue(id);if(token!==issuePageToken||location.hash!==issueHref(id))return;if(!i)return notFound();const e=entity(id);setPage('課題の詳細',`<div class="page"><a class="back-link" href="#issues">${icon('arrow')}地域のお困りごとに戻る</a><section class="panel content-block"><div class="card-topline">${issueBadge(i)}<a class="button small" href="#issue-edit/${encodeURIComponent(id)}">${icon('edit')}編集・状態を変更</a></div><h1 class="issue-detail-title">${esc(i.title)}</h1><div class="issue-detail-meta"><span>${esc(i.category||'分野未設定')}</span><span>投稿者：${esc(i.reporter_name||'名前未記入')}${i.reporter_role?`（${esc(i.reporter_role)}）`:''}</span><span>${i.updated_at?'更新 '+esc(i.updated_at.slice(0,10)):''}</span></div><div class="issue-body"><h2>どんなことに困っていますか？</h2><div class="prose">${prose(i.description)||'<p class="muted">詳しい内容はまだ登録されていません。</p>'}</div>${i.background?`<h2>背景・これまでの経緯</h2><div class="prose">${prose(i.background)}</div>`:''}${i.desired_support?`<h2>一緒に考えてほしいこと</h2><div class="prose">${prose(i.desired_support)}</div>`:''}</div></section><div class="detail-layout lab-section"><section class="panel content-block"><h2>地域・施設と場所</h2><p class="issue-location-label">${icon('pin')}${esc(i.targets.map(t=>t.title).join('・')||'地域・現場未設定')}</p><div class="tags">${i.targets.map(t=>`<a class="tag" href="#entity/${encodeURIComponent(t.id)}">${esc(t.title)}</a>`).join('')}</div>${issuePosition(i)?`<p class="fine">${esc(issueLocationMeaning(i))}</p><div id="issues-map" class="issue-map issue-detail-map" aria-label="課題の場所"></div><p class="hint" id="issue-map-message">位置情報は、関連する地域・現場に登録されています。</p>`:'<p class="hint">位置を登録した地域・現場はまだ紐づいていません。場所が分かった時点で現場を追加できます。</p>'}</section><aside class="panel content-block"><h2>この課題に取り組む研究・活動</h2><div class="stack lab-section">${i.research.map(r=>`<a class="quiet-link" href="${activityHref(r.id)}">${esc(r.title)} →</a>`).join('')||'<p class="hint">まだ紐づいている研究・活動はありません。</p>'}</div><button class="button primary small lab-section" data-issue-research="${esc(id)}">${icon('plus')}この課題から研究を始める</button><a class="button small lab-section" href="#issue-edit/${encodeURIComponent(id)}">既存の研究・活動を関連付ける</a><p class="hint">研究との関連付けと、課題の解決状況は別々に記録します。</p></aside></div>${e?.wiki_content?`<section class="panel content-block lab-section"><h2>これまでの記述</h2><div class="prose">${prose(e.wiki_content)}</div></section>`:''}</div>`,'issues');if(issuePosition(i))createIssueMap('issues-map',[i]);}catch(error){if(token===issuePageToken)setPage('課題の詳細',`<div class="page"><p role="alert">${esc(error.message)}</p><a class="button" href="#issues">掲示板に戻る</a></div>`,'issues');}}
function issueTextField(key,label,value,rows=0,placeholder=''){return `<div class="field"><label for="issue-${key}">${label}</label>${rows?`<textarea id="issue-${key}" name="${key}" rows="${rows}" maxlength="${key==='description'?12000:key==='background'?6000:3000}" placeholder="${esc(placeholder)}">${esc(value||'')}</textarea>`:`<input id="issue-${key}" name="${key}" value="${esc(value||'')}" maxlength="200" placeholder="${esc(placeholder)}" ${key==='title'?'required':''}>`}</div>`;}
async function renderIssueEditor(id=''){
  const token=++issuePageToken;destroyIssueMap();
  let i={status:'相談受付',targets:[],research:[],location_precision:'おおよその場所'};
  if(id){try{i=await getIssue(id);if(token!==issuePageToken)return;if(!i)return notFound();}catch(error){notify(error.message);return;}}
  const draftKey='ritou-issue-draft:'+(id||'new'),draft=storage.get(draftKey);
  if(draft&&(!id||draft.expected_revision===i.revision))i={...i,...draft,targets:(draft.target_ids||[]).map(id=>({id})),research:(draft.research_ids||[]).map(id=>({id}))};
  setPage(id?'困りごとを編集':'困りごとを書く',`<div class="page issue-editor resident-issue-editor">
    <a class="back-link" href="${id?issueHref(id):'#issues'}">${icon('arrow')}戻る</a>
    <div class="page-heading"><div><h1>${id?'困りごとを編集する':'困りごとを書く'}</h1><p>身近な「困った」を、いつもの言葉で。解決方法が分からなくても大丈夫です。</p></div></div>
    <form id="issue-form" data-id="${esc(id)}" data-revision="${esc(i.revision||'')}">
      <div class="form-error" id="issue-error" role="alert" hidden></div>
      <section class="panel form-section issue-main-fields">
        ${issueTextField('title','どんな困りごとですか？ <span class="required">必須</span>',i.title,0,'例：保育所前の坂道で、車がすれ違えない')}
        ${issueTextField('description','もう少し詳しく <span class="optional">任意</span>',i.description,6,'いつ、どこで、どんなことで困っていますか？\n思いつく範囲で自由に書いてください。')}
        <p class="hint">短い文章でも大丈夫。タイトルだけでも投稿できます。</p>
      </section>
      <p class="issue-options-intro">伝えておきたいことがあれば、追加できます。すべて任意です。</p>
      <details class="panel issue-form-option" ${i.targets.length||i.new_target?'open':''}>
        <summary>場所を添える <span class="optional">任意</span></summary><div class="issue-option-body">
          <p class="hint">分かる場所にチェックを入れるか、下に場所の名前を書いてください。</p>
          <div class="issue-place-choices" role="group" aria-label="関係する場所">${data.entities.filter(e=>e.types.includes('Target')).map(e=>`<label><input type="checkbox" name="target_ids" value="${esc(e.id)}" ${i.targets.some(t=>t.id===e.id)?'checked':''}><span>${esc(title(e))}</span></label>`).join('')||'<p class="hint">登録済みの場所はまだありません。</p>'}</div>
          <div id="issue-selected-places"></div>
          ${issueTextField('new_target','一覧にない場所の名前',i.new_target,0,'例：保育所前の坂道、〇〇集会所の近く')}
          <details id="issue-position-option" class="issue-position-option"><summary>地図で場所を示す <span class="optional">任意</span></summary>
            <p class="hint">上に場所の名前を書き、地図を押してください。だいたいの位置で構いません。</p>
            <div id="issue-picker-map" class="issue-map issue-picker-map" aria-label="場所の位置を選ぶ"></div>
            <p class="hint" id="issue-map-message">地図を押すと、その場所に印が付きます。</p>
            <input type="hidden" id="issue-new_target_latitude" name="new_target_latitude" value="${i.new_target_latitude??''}">
            <input type="hidden" id="issue-new_target_longitude" name="new_target_longitude" value="${i.new_target_longitude??''}">
            <input type="hidden" name="new_target_precision" value="${esc(i.new_target_precision||'おおよその場所')}">
            <button class="button small" type="button" data-issue-clear-position>地図の印を消す</button>
          </details>
        </div>
      </details>
      <details class="panel issue-form-option" ${i.reporter_name||i.reporter_role?'open':''}>
        <summary>投稿する方について <span class="optional">任意</span></summary><div class="issue-option-body">
          ${issueTextField('reporter_name','お名前・ニックネーム・団体名',i.reporter_name,0,'空欄でも投稿できます')}
          <div class="field"><label for="issue-role">あなたの立場</label><select id="issue-role" name="reporter_role">${issueOptions(['住民','行政','学生','教員','地域団体・事業者','その他'],i.reporter_role,'選ばなくても大丈夫です')}</select></div>
          <p class="hint">ここに書いた名前は掲示板に表示されます。公開してよい名前をご記入ください。</p>
        </div>
      </details>
      <details class="panel issue-form-option" ${i.background||i.desired_support||i.category?'open':''}>
        <summary>ほかに伝えたいこと <span class="optional">任意</span></summary><div class="issue-option-body">
          ${issueTextField('background','これまでに試したこと・経緯',i.background,3)}
          ${issueTextField('desired_support','こうなるとうれしい・手伝ってほしいこと',i.desired_support,3,'例：一度現場を見て、一緒に考えてほしい')}
          <div class="field"><label for="issue-category">困りごとの分野</label><select id="issue-category" name="category">${issueOptions(issueCategories,i.category,'分からない・選ばない')}</select></div>
        </div>
      </details>
      ${id?`<details class="panel issue-form-option"><summary>進み具合・研究とのつながりを編集</summary><div class="issue-option-body">
        <p class="hint">投稿後の相談や取り組みが進んだときに更新できます。</p>
        <div class="field"><label for="issue-status">現在の状態</label><select id="issue-status" name="status">${issueOptions(issueStates,i.status)}</select></div>
        <fieldset class="issue-research-choices"><legend>この課題に取り組む研究・活動</legend>${data.entities.filter(e=>e.types.includes('Research')).map(e=>`<label><input type="checkbox" name="research_ids" value="${esc(e.id)}" ${i.research.some(r=>r.id===e.id)?'checked':''}><span>${esc(title(e))}</span></label>`).join('')}</fieldset>
      </div></details>`:'<input type="hidden" name="status" value="相談受付">'}
      <div class="issue-form-actions"><span class="hint" id="issue-draft-state">入力内容はこのブラウザに下書き保存されます。</span><button class="button primary" type="submit" id="issue-save">${id?'変更を保存する':'困りごとを投稿する'}</button></div>
    </form></div>`,'issues');
  $('topbar-actions').innerHTML='';
  let marker=null;
  function updateMarker(){
    if(!issueMap)return;const lat=$('issue-new_target_latitude').value,lon=$('issue-new_target_longitude').value;
    if(marker){issueMap.removeLayer(marker);marker=null;}
    if(lat!==''&&lon!==''&&Number.isFinite(+lat)&&Number.isFinite(+lon)&&Math.abs(+lat)<=90&&Math.abs(+lon)<=180){
      marker=L.circleMarker([+lat,+lon],{radius:10,color:'#1c6c6c',fillOpacity:.7}).addTo(issueMap);
      $('issue-map-message').textContent='この印の場所を、入力した場所の名前と一緒に保存します。';
    }else $('issue-map-message').textContent='地図を押すと、その場所に印が付きます。';
  }
  function refreshPlaces(){
    const places=Array.from(document.querySelectorAll('#issue-form input[name="target_ids"]:checked')).map(o=>entity(o.value)).filter(Boolean).map(placeFromEntity);
    $('issue-selected-places').innerHTML=places.map(p=>`<p class="hint">${esc(p.title)} ${Number.isFinite(p.latitude)&&Number.isFinite(p.longitude)?'· 地図に表示できます':''}</p>`).join('');
    if(!$('issue-position-option').open)return;
    destroyIssueMap();marker=null;
    createIssueMap('issue-picker-map',[],event=>{$('issue-new_target_latitude').value=event.latlng.lat.toFixed(6);$('issue-new_target_longitude').value=event.latlng.lng.toFixed(6);updateMarker();saveIssueDraft();},places);updateMarker();
  }
  refreshPlaces();$('issue-position-option').addEventListener('toggle',refreshPlaces);
  document.querySelectorAll('#issue-form input[name="target_ids"]').forEach(el=>el.addEventListener('change',refreshPlaces));
  $('issue-form').addEventListener('input',()=>{updateMarker();saveIssueDraft();});
  $('issue-form').addEventListener('change',saveIssueDraft);$('issue-form').addEventListener('submit',saveIssueForm);
}

function issueFormPayload(){const form=$('issue-form'),f=new FormData(form),body={issue_id:form.dataset.id,expected_revision:form.dataset.revision};for(const key of ['title','description','background','desired_support','status','category','reporter_name','reporter_role','new_target','new_target_precision'])body[key]=f.get(key)||'';body.target_ids=f.getAll('target_ids');body.research_ids=f.getAll('research_ids');for(const key of ['new_target_latitude','new_target_longitude'])body[key]=f.get(key)===''?null:Number(f.get(key));return body;}
function saveIssueDraft(){const payload=issueFormPayload();const ok=storage.set('ritou-issue-draft:'+(payload.issue_id||'new'),payload);$('issue-draft-state').textContent=ok?'このブラウザに下書きを保存しました。':'下書きを保存できません。画面を閉じる前に投稿してください。';}
async function saveIssueForm(event){event.preventDefault();const button=$('issue-save');if(button.disabled)return;button.disabled=true;const body=issueFormPayload();$('issue-error').hidden=true;try{const result=await request('/api/issues',body);storage.remove('ritou-issue-draft:'+(body.issue_id||'new'));await load();location.hash=issueHref(result.issue_id);notify('課題を保存しました');}catch(error){$('issue-error').textContent=error.message;$('issue-error').hidden=false;$('issue-error').scrollIntoView({block:'center'});button.disabled=false;}}
document.addEventListener('input',event=>{if(event.target.id==='issue-search'){issueQuery=event.target.value;drawIssueResults();}});
document.addEventListener('change',event=>{if(event.target.id==='issue-category-filter')issueCategory=event.target.value;else if(event.target.id==='issue-role-filter')issueRole=event.target.value;else if(event.target.id==='issue-unlinked')issueOnlyUnlinked=event.target.checked;else return;drawIssueResults();});
document.addEventListener('click',event=>{const view=event.target.closest('[data-issue-view]');if(view){issueView=view.dataset.issueView;history.replaceState(null,'','#issues/'+issueView);document.querySelectorAll('[data-issue-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.issueView===issueView)));drawIssueResults();}if(event.target.closest('[data-issue-clear-position]')){$('issue-new_target_latitude').value='';$('issue-new_target_longitude').value='';$('issue-new_target_latitude').dispatchEvent(new Event('input',{bubbles:true}));}const research=event.target.closest('[data-issue-research]');if(research){startEditor();editor.issues=uniq([...editor.issues,research.dataset.issueResearch]);editor.targets=uniq([...editor.targets,...(entity(research.dataset.issueResearch)?.incoming.filter(r=>r.predicate==='抱える').map(r=>r.subject_id)||[])]);persist();renderEditor();}if(event.target.closest('[data-issue-retry]'))renderIssues();});

async function fetchPlaces(){const response=await fetch('/api/places');if(!response.ok)throw new Error('地域・現場を読み込めませんでした');return (await response.json()).items;}
async function renderPlace(id){
  const token=++issuePageToken;destroyIssueMap();setPage('地域・現場','<div class="page loading">現場を読み込んでいます…</div>','explore');
  try{
    const [places,items]=await Promise.all([fetchPlaces(),fetchIssues()]);if(token!==issuePageToken)return;
    const place=places.find(p=>p.id===id);if(!place)return notFound();
    const related=items.filter(i=>i.targets.some(t=>t.id===id)),e=entity(id);
    const projects=data.entities.filter(p=>p.types.includes('Research')&&out(p,'対象とする').includes(id));
    setPage('地域・現場',`<div class="page"><a class="back-link" href="#explore">${icon('arrow')}地域・課題から探す</a><section class="panel content-block"><div class="eyebrow">PLACE</div><h1>${esc(place.title)}</h1><p class="description">${esc(place.summary)}</p><div class="place-detail-actions"><a class="button small" href="#place-edit/${encodeURIComponent(id)}">${icon('edit')}名前・位置を編集</a><a class="button small" href="#place-new">地域・現場を追加</a></div>${Number.isFinite(place.latitude)&&Number.isFinite(place.longitude)?`<div class="lab-section"><p class="fine">${esc(place.location_precision)} · ${place.latitude}, ${place.longitude}</p><div id="issues-map" class="issue-map issue-detail-map" aria-label="地域・現場の位置"></div><p class="hint" id="issue-map-message">この地域・現場の位置を、関連するお困りごとの地図でも表示します。</p></div>`:'<p class="hint lab-section">位置は未設定です。場所が分かってから追加できます。</p>'}${e?.wiki_content?`<div class="prose lab-section">${prose(e.wiki_content)}</div>`:''}</section><section class="lab-section"><div class="section-heading"><h2>この地域・現場のお困りごと <span class="fine">${related.length}件</span></h2><a class="quiet-link" href="#issues/map">地図で探す →</a></div><div class="entity-grid">${related.map(issueCard).join('')||'<p class="hint">関連する困りごとはまだありません。</p>'}</div></section><section class="lab-section"><h2>関連する研究・活動</h2><div class="stack lab-section">${projects.map(p=>projectCard(p)).join('')||'<p class="hint">関連する取り組みはまだありません。</p>'}</div></section></div>`,'explore');
    if(Number.isFinite(place.latitude)&&Number.isFinite(place.longitude))createIssueMap('issues-map',related,null,[place]);
  }catch(error){if(token===issuePageToken)setPage('地域・現場',`<div class="page"><p role="alert">${esc(error.message)}</p><a href="#explore">一覧に戻る</a></div>`,'explore');}
}
async function renderPlaceEditor(id=''){
  const token=++issuePageToken;destroyIssueMap();let place={title:'',summary:'',latitude:null,longitude:null,location_precision:'おおよその場所'};
  if(id){try{place=(await fetchPlaces()).find(p=>p.id===id);if(token!==issuePageToken)return;if(!place)return notFound();}catch(error){notify(error.message);return;}}
  const back=id?'#entity/'+encodeURIComponent(id):'#explore';
  setPage(id?'地域・現場を編集':'地域・現場を登録',`<div class="page issue-editor"><a class="back-link" href="${back}">${icon('arrow')}戻る</a><div class="page-heading"><div><div class="eyebrow">PLACE</div><h1>${id?'地域・現場を編集する':'地域・現場を登録する'}</h1><p>施設・集落・道路・空き地など、課題がある場所を登録します。仮の呼び名でも構いません。</p></div></div><form id="place-form"><section class="panel form-section"><div class="form-error" id="place-error" role="alert" hidden></div><div class="field"><label for="place-title">地域・現場の名前 <span class="required">必須</span></label><input id="place-title" name="title" required maxlength="200" value="${esc(place.title)}" placeholder="例：弓削保育所、保育所前の坂道"></div><div class="field"><label for="place-summary">どのような場所ですか？</label><textarea id="place-summary" name="summary" rows="3" maxlength="6000">${esc(place.summary)}</textarea></div><p class="hint">地図をクリックするか、緯度・経度を入力してください。位置がまだ分からない場合は空欄でも登録できます。</p><div id="place-picker-map" class="issue-map issue-picker-map" aria-label="地域・現場の位置を選ぶ"></div><p id="issue-map-message" class="hint">位置情報は、この地域・現場に紐づけて保存します。</p><div class="two-fields">${['latitude','longitude'].map((key,n)=>`<div class="field"><label for="place-${key}">${n?'経度':'緯度'}</label><input id="place-${key}" name="${key}" type="number" step="any" min="${n?-180:-90}" max="${n?180:90}" value="${place[key]??''}"></div>`).join('')}</div><div class="field"><label for="place-precision">位置の意味</label><select id="place-precision" name="location_precision">${issueOptions(['おおよその場所','施設・現場の位置'],place.location_precision)}</select></div><button class="button small" type="button" id="place-clear-position">位置を未設定にする</button></section><div class="issue-form-actions"><a class="button" href="${back}">戻る</a><button class="button primary" type="submit" id="place-save">地域・現場を保存する</button></div></form></div>`,'explore');
  let marker=null;
  function update(){if(!issueMap)return;if(marker){issueMap.removeLayer(marker);marker=null;}const lat=$('place-latitude').value,lon=$('place-longitude').value;if(lat!==''&&lon!==''&&Number.isFinite(+lat)&&Number.isFinite(+lon)&&Math.abs(+lat)<=90&&Math.abs(+lon)<=180)marker=L.marker([+lat,+lon],{title:$('place-title').value||'選択した現場',icon:L.divIcon({className:'place-marker',html:'<span class="place-pin"><span>●</span></span>',iconSize:[34,42],iconAnchor:[17,42]})}).addTo(issueMap);}
  createIssueMap('place-picker-map',[],event=>{$('place-latitude').value=event.latlng.lat.toFixed(6);$('place-longitude').value=event.latlng.lng.toFixed(6);update();});update();if(issueMap&&Number.isFinite(place.latitude)&&Number.isFinite(place.longitude))issueMap.setView([place.latitude,place.longitude],16);
  $('place-form').addEventListener('input',update);
  $('place-clear-position').addEventListener('click',()=>{$('place-latitude').value='';$('place-longitude').value='';update();});
  $('place-form').addEventListener('submit',async event=>{event.preventDefault();const button=$('place-save');if(button.disabled)return;button.disabled=true;$('place-error').hidden=true;const form=new FormData(event.target),body=Object.fromEntries(form);body.target_id=id;body.expected_revision=place.revision||'';for(const key of ['latitude','longitude'])body[key]=body[key]===''?null:Number(body[key]);try{const saved=await request('/api/places',body);await load();location.hash='#entity/'+encodeURIComponent(saved.target_id);notify('地域・現場を保存しました');}catch(error){$('place-error').hidden=false;$('place-error').textContent=error.message;$('place-error').scrollIntoView({block:'center'});button.disabled=false;}});
}
