'use strict';

const get = id => document.getElementById(id);
const feedback = get('account-feedback');
let adminToken = '';
let setupRequired = false;

async function api(path, {body, method = body ? 'POST' : 'GET', admin = false} = {}) {
  const headers = {'X-Ritou-Account': '1'};
  if (body) headers['Content-Type'] = 'application/json';
  if (admin) headers.Authorization = 'Bearer ' + adminToken;
  const response = await fetch('/api/account' + path, {
    method, headers, body: body ? JSON.stringify(body) : undefined, cache: 'no-store'
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(typeof result.detail === 'string' ? result.detail : '入力内容を確認してください');
  }
  return result;
}

async function run(button, action) {
  button.disabled = true;
  feedback.textContent = '';
  try {
    await action();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function submit(id, action) {
  get(id).addEventListener('submit', event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    run(event.submitter, () => action(values));
  });
}

async function refresh() {
  const {account, ishibashi_setup_required} = await api('');
  setupRequired = ishibashi_setup_required;
  get('open-ishibashi-setup').hidden = !setupRequired;
  get('setup-ishibashi').hidden = !setupRequired || !adminToken;
  get('signed-out').hidden = Boolean(account);
  get('signed-in').hidden = !account;
  get('account-login-id').textContent = account?.login_id || '';
  get('claim-section').hidden = !account || Boolean(account.person_id);
  get('profile-link').hidden = !account?.person_id;
  if (account?.person_id) get('profile-link').href = '/#person/' + encodeURIComponent(account.person_id);
  get('claim-status').textContent = account?.claim
    ? '承認待ちです。別の申請を送ると、現在の申請が置き換わります。' : '';
}

submit('login', async body => {
  await api('/login', {body});
  get('login').reset();
  await refresh();
});
submit('register', async body => {
  await api('/register', {body});
  get('register').reset();
  await refresh();
});
get('logout').addEventListener('click', event => run(event.currentTarget, async () => {
  await api('/logout', {method: 'POST'});
  get('claim').reset();
  get('search-people').reset();
  get('person-options').replaceChildren();
  updateClaimChoice();
  await refresh();
}));

function describe(person) {
  return [person.name, person.affiliations.join('・'), person.headline].filter(Boolean).join(' ／ ');
}

submit('search-people', async ({q}) => {
  const matches = await api('/people?q=' + encodeURIComponent(q));
  const labels = matches.map(person => {
    const label = document.createElement('label');
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'person_id';
    radio.value = person.id;
    label.append(radio, ' ' + describe(person));
    return label;
  });
  get('person-options').replaceChildren(...labels);
  updateClaimChoice();
  if (!labels.length) feedback.textContent = '一致する人物は見つかりませんでした。';
});

function updateClaimChoice() {
  const selected = get('claim').querySelector('[name=person_id]:checked');
  const createsPerson = selected?.value === '';
  get('new-name').hidden = !createsPerson;
  get('claim').elements.name.required = createsPerson;
}

get('claim').addEventListener('change', updateClaimChoice);
submit('claim', async body => {
  await api('/claim', {body: {...body, person_id: body.person_id || null}});
  get('claim').reset();
  updateClaimChoice();
  await refresh();
  feedback.textContent = '申請を送りました。';
});

async function showClaims() {
  const claims = await api('/claims', {admin: true});
  const cards = claims.map(claim => {
    const card = document.createElement('article');
    card.className = 'panel form-section stack';
    const heading = document.createElement('h3');
    heading.textContent = claim.person ? describe(claim.person) : '新しい人物 ／ ' + claim.name;
    const detail = document.createElement('p');
    detail.textContent = '申請者のログインID ' + claim.login_id;
    const actions = document.createElement('div');
    for (const [decision, label] of [['approve', '承認'], ['reject', '却下']]) {
      const button = document.createElement('button');
      button.className = 'button';
      button.textContent = label;
      button.addEventListener('click', () => {
        if (!window.confirm('この申請を' + label + 'しますか？')) return;
        run(button, async () => {
          await api('/claims/' + encodeURIComponent(claim.id) + '/' + decision, {method: 'POST', admin: true});
          await showClaims();
          if (decision === 'approve') await refresh();
        });
      });
      actions.append(button, ' ');
    }
    card.append(heading, detail, actions);
    return card;
  });
  get('claim-list').replaceChildren(...(cards.length ? cards : ['承認待ちの申請はありません。']));
}

get('open-ishibashi-setup').addEventListener('click', () => {
  get('admin-section').open = true;
  get('admin').elements.token.focus();
});

function resetAdminPanel() {
  get('setup-ishibashi').hidden = true;
  get('setup-ishibashi').reset();
  get('setup-status').textContent = '';
  get('claims-heading').hidden = true;
  get('claim-list').replaceChildren();
}

submit('admin', async ({token}) => {
  resetAdminPanel();
  adminToken = token.trim();
  get('admin').reset();
  await showClaims();
  get('setup-ishibashi').hidden = !setupRequired;
  get('claims-heading').hidden = false;
});

submit('setup-ishibashi', async body => {
  try {
    await api('/setup-ishibashi', {body, admin: true});
    await refresh();
    feedback.textContent = '設定しました。ID i22002 でログインできます。';
  } catch (error) {
    get('setup-status').textContent = error.message;
  } finally {
    get('setup-ishibashi').reset();
  }
});

get('admin-section').addEventListener('toggle', event => {
  if (event.currentTarget.open) return;
  adminToken = '';
  resetAdminPanel();
  get('admin').reset();
});

refresh().catch(error => { feedback.textContent = error.message; });
