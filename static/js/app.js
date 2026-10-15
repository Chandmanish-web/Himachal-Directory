(() => {
  'use strict';

  // These elements are shared by every page rendered by this single-page app.
  const $app = document.getElementById('app');
  const $header = document.getElementById('header');
  // Data that is needed by more than one page.
  const state = { meta: { categories: [], cities: [] }, user: null };
  // Prevent a slow response from replacing a page the visitor has already left.
  let renderToken = 0;

  /* ---------- helpers ---------- */
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  }

  async function api(path, options = {}) {
    const requestOptions = {
      method: options.method || 'GET',
      headers: { 'X-Requested-With': 'fetch' },
      credentials: 'same-origin',
    };
    if (options.body !== undefined) {
      requestOptions.headers['Content-Type'] = 'application/json';
      requestOptions.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch('/api' + path, requestOptions);
    } catch {
      const error = new Error('Could not reach the server. Check your connection and try again.');
      error.fields = {};
      throw error;
    }

    let responseData = {};
    try {
      responseData = await response.json();
    } catch {
      // A successful empty response is allowed; errors below use a helpful fallback.
    }
    if (!response.ok) {
      const error = new Error(responseData.error || 'Something went wrong. Please try again.');
      error.status = response.status;
      error.fields = responseData.fields || {};
      throw error;
    }
    return responseData;
  }

  function setMeta(title, description) {
    document.title = title ? `${title} — Himachal Industrial Directory` : 'Himachal Industrial Directory — Good businesses. Real connections.';
    const descriptionTag = document.querySelector('meta[name="description"]');
    if (descriptionTag && description) {
      descriptionTag.setAttribute('content', description);
    }
  }

  function navigate(url, replace = false) {
    // Update the address bar without a full page reload, then render that route.
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
    render();
  }

  function optionList(options, selectedValue, allLabel) {
    const firstOption = allLabel ? `<option value="">${escapeHtml(allLabel)}</option>` : '';
    const remainingOptions = options.map((option) =>
      `<option value="${escapeHtml(option)}"${option === selectedValue ? ' selected' : ''}>${escapeHtml(option)}</option>`
    ).join('');
    return firstOption + remainingOptions;
  }

  function formatDate(dateValue) {
    try {
      return new Date(dateValue).toLocaleDateString('en-IN', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      });
    } catch {
      return '';
    }
  }

  function snippet(text, maximumLength = 130) {
    return text.length > maximumLength
      ? text.slice(0, maximumLength).trimEnd() + '…'
      : text;
  }

  function field(name, label, value = '', options = {}) {
    const type = options.type || 'text';
    const hint = options.hint ? `<div class="hint">${escapeHtml(options.hint)}</div>` : '';
    const attributes = `id="f-${name}" name="${name}" ${options.required ? 'required' : ''} ${options.autocomplete ? `autocomplete="${options.autocomplete}"` : ''} ${options.maxlength ? `maxlength="${options.maxlength}"` : ''}`;
    let control;
    if (type === 'textarea') {
      control = `<textarea ${attributes}>${escapeHtml(value)}</textarea>`;
    } else if (type === 'select') {
      control = `<select ${attributes}>${optionList(options.options, value, options.placeholder)}</select>`;
    } else {
      control = `<input type="${type}" ${attributes} value="${escapeHtml(value)}" ${options.minlength ? `minlength="${options.minlength}"` : ''}>`;
    }
    return `<div class="field"><label for="f-${name}">${escapeHtml(label)}</label>${control}${hint}<div class="err" data-err="${name}" role="alert"></div></div>`;
  }

  function formValues(form) {
    const values = {};
    new FormData(form).forEach((value, name) => {
      values[name] = typeof value === 'string' ? value : '';
    });
    return values;
  }

  function showFormError(form, error) {
    form.querySelectorAll('[data-err]').forEach((n) => { n.textContent = ''; });
    const message = form.querySelector('.form-msg');
    if (message) {
      message.className = 'form-msg error';
      message.textContent = error.message;
      message.hidden = false;
    }
    Object.entries(error.fields || {}).forEach(([fieldName, fieldError]) => {
      const fieldMessage = form.querySelector(`[data-err="${fieldName}"]`);
      if (fieldMessage) fieldMessage.textContent = fieldError;
    });
  }

  function showFormOk(form, text) {
    form.querySelectorAll('[data-err]').forEach((n) => { n.textContent = ''; });
    const message = form.querySelector('.form-msg');
    if (message) {
      message.className = 'form-msg ok';
      message.textContent = text;
      message.hidden = false;
    }
  }

  async function submitForm(form, handler) {
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    try { await handler(); }
    catch (error) { showFormError(form, error); }
    finally { if (submitButton) submitButton.disabled = false; }
  }

  function businessCard(business) {
    const phone = business.phone
      ? `<a class="biz-phone" href="tel:${escapeHtml(business.phone.replace(/[^+0-9]/g, ''))}"><span>Phone</span>${escapeHtml(business.phone)}</a>`
      : '<span class="biz-phone biz-phone-missing"><span>Phone</span>Not provided</span>';
    return `<article class="biz">
      <div class="tags"><span class="tag">${escapeHtml(business.category)}</span><span class="tag tag-city">${escapeHtml(business.city)}</span></div>
      <h3><a href="/business/${escapeHtml(business.slug)}">${escapeHtml(business.name)}</a></h3>
      <p>${escapeHtml(snippet(business.description))}</p>
      <div class="biz-card-footer">${phone}<a class="biz-details" href="/business/${escapeHtml(business.slug)}">View details <span aria-hidden="true">→</span></a></div>
    </article>`;
  }

  function searchForm(values = {}, cls = 'search-row') {
    return `<form class="${cls}" data-search role="search">
      <div class="field"><label for="s-q">Business, product or service</label>
        <input id="s-q" name="q" type="search" value="${escapeHtml(values.q || '')}" placeholder="e.g. timber, homestay, cold storage" maxlength="100"></div>
      <div class="field"><label for="s-category">Category</label>
        <select id="s-category" name="category">${optionList(state.meta.categories, values.category, 'All categories')}</select></div>
      <div class="field"><label for="s-city">City</label>
        <select id="s-city" name="city">${optionList(state.meta.cities, values.city, 'All of Himachal')}</select></div>
      <button class="btn btn-apple" type="submit">Search businesses</button>
    </form>`;
  }

  function bindSearch(rootElement) {
    const form = rootElement.querySelector('[data-search]');
    if (!form) return;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const values = formValues(form);
      const searchParameters = new URLSearchParams();
      Object.entries(values).forEach(([name, value]) => {
        if (value.trim()) searchParameters.set(name, value.trim());
      });
      navigate('/explore' + (searchParameters.toString() ? '?' + searchParameters : ''));
    });
  }

  const mountainSvg = `<svg viewBox="0 0 600 400" role="img" aria-label="Illustration of Himalayan ridges and pine trees at sunrise">
    <circle cx="470" cy="110" r="44" fill="#e9a07f" opacity=".95"/>
    <g class="ridge r1"><path d="M0 250 L60 190 L110 225 L190 120 L250 200 L310 150 L390 235 L450 185 L520 235 L600 200 L600 400 L0 400Z" fill="#4d7a6b"/>
      <path d="M190 120 L214 154 L196 146 L180 158 L170 142Z" fill="#eef3ef"/><path d="M310 150 L328 176 L314 170 L300 180Z" fill="#eef3ef"/></g>
    <g class="ridge r2"><path d="M0 300 L80 235 L140 275 L230 205 L300 275 L370 230 L450 290 L530 245 L600 285 L600 400 L0 400Z" fill="#2e5d4f"/></g>
    <g class="ridge r3"><path d="M0 345 L70 320 L150 340 L240 310 L330 342 L420 318 L510 340 L600 322 L600 400 L0 400Z" fill="#14322b" stroke="#1f4a3f" stroke-width="2"/>
      <g fill="#0d231e"><path d="M40 345 l12 -44 l12 44z M62 350 l10 -36 l10 36z M520 342 l12 -46 l12 46z M545 346 l10 -34 l10 34z M110 352 l8 -30 l8 30z M470 350 l9 -32 l9 32z"/></g></g>
  </svg>`;

  /* ---------- header ---------- */
  function renderHeader() {
    const u = state.user;
    const account = u
      ? `<a href="/dashboard">${escapeHtml(u.name.split(' ')[0])}</a><a href="#" data-logout>Sign out</a>`
      : `<a href="/login">Account</a>`;
    $header.innerHTML = `<div class="wrap header-row">
      <a href="/" class="brand"><span class="brand-name">Himachal Industrial Directory</span><span class="brand-tag">Local connections. Limitless possibilities.</span></a>
      <button class="nav-toggle" aria-expanded="false" aria-controls="nav">Menu</button>
      <nav class="nav" id="nav" aria-label="Main">
        <a href="/explore">Explore businesses</a><a href="/#categories">Categories</a><a href="/#cities">Cities</a>
        <a href="/about">About us</a>${account}
        <a class="btn btn-sm" href="/dashboard">Add your business</a>
      </nav></div>`;
    const toggle = $header.querySelector('.nav-toggle');
    const nav = $header.querySelector('.nav');
    toggle.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
    nav.addEventListener('click', (e) => {
      if (e.target.closest('a')) { nav.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); }
    });
    const out = $header.querySelector('[data-logout]');
    if (out) out.addEventListener('click', async (e) => {
      e.preventDefault();
      try { await api('/logout', { method: 'POST', body: {} }); } catch { /* ignore */ }
      state.user = null; renderHeader(); navigate('/');
    });
  }

  /* ---------- pages ---------- */
  async function pageHome() {
    setMeta('', 'Discover businesses, manufacturers and local services across Himachal Pradesh. Find your next connection in Shimla, Dharamshala, Solan, Baddi and beyond.');
    const { categories, cities } = state.meta;
    $app.innerHTML = `
      <section class="hero">
        <div class="hero-grid">
          <div>
            <div class="eyebrow">Himachal's business discovery platform</div>
            <h1>Find local expertise. Build <em>great connections.</em></h1>
            <p class="hero-sub">From your neighbourhood essentials to Himachal's industrial leaders. Discover the right business, in the right place.</p>
          </div>
          <div class="hero-art">${mountainSvg}
            <div class="hero-caption">Shimla, Himachal Pradesh<strong>A world of business. <em>Closer than you think.</em></strong></div>
          </div>
        </div>
      </section>
      <div class="search-wrap"><div class="search-card">
        ${searchForm()}
        <div class="popular"><strong>Popular searches:</strong>
          <a href="/explore?category=${encodeURIComponent('Manufacturers & factories')}">Manufacturers</a>
          <a href="/explore?category=${encodeURIComponent('Healthcare & medical')}">Healthcare</a>
          <a href="/explore?category=${encodeURIComponent('Restaurants & food')}">Restaurants</a>
          <a href="/explore?category=${encodeURIComponent('Home & repair')}">Home services</a>
        </div></div></div>

      <section class="section" id="categories"><div class="wrap">
        <div class="section-head"><div><div class="eyebrow">01 / Find your fit</div><h2>A business for every need.</h2></div>
          <a class="link-arrow" href="/explore">View all categories</a></div>
        <div class="cat-grid">${categories.slice(0, 8).map((category) => `<a class="cat" href="/explore?category=${encodeURIComponent(category)}">${escapeHtml(category)}<small>Browse listings</small></a>`).join('')}</div>
      </div></section>

      <section class="section cities" id="cities"><div class="wrap">
        <div class="section-head"><div><div class="eyebrow">02 / Explore your region</div><h2>Hill roots. <em>Statewide reach.</em></h2>
          <p>Big towns, growing valleys, and everything in between. Find expertise where you need it.</p></div></div>
        <div class="city-grid">${cities.slice(0, 6).map((city, index) => `<a class="city" href="/explore?city=${encodeURIComponent(city)}"><span>${String(index + 1).padStart(2, '0')}</span>${escapeHtml(city)}</a>`).join('')}</div>
      </div></section>

      <section class="section-tight"><div class="wrap">
        <div class="section-head"><div><div class="eyebrow">Everyday needs / local solutions</div><h2>A little closer to home.</h2>
          <p>The people and services that keep your day moving.</p></div></div>
        <div class="everyday">
          ${['Home & repair', 'Professional services', 'Beauty & personal care', 'Transport & logistics'].map((category) => `<a href="/explore?category=${encodeURIComponent(category)}">${escapeHtml(category)}</a>`).join('')}
        </div>
      </div></section>

      <section class="section-tight"><div class="wrap">
        <div class="ad-slot"><div><small>Advertisement space / not currently bookable</small><strong>Room for your next big idea.</strong></div>
          <a class="btn btn-ghost" href="/about#advertise">About business visibility</a></div>
      </div></section>

      <section class="section"><div class="wrap">
        <div class="section-head"><div><div class="eyebrow">03 / Meet the local community</div><h2>New names. New possibilities.</h2></div>
          <a class="link-arrow" href="/explore">Explore businesses</a></div>
        <div id="latest"><div class="list-grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
      </div></section>

      <section class="section-tight"><div class="wrap trust">
        <div><div class="trust-badge">HP</div><div class="eyebrow">The enterprising spirit of Himachal</div>
          <h2>Built here. <em>Connected everywhere.</em></h2>
          <p>Discover manufacturers, suppliers, and industrial expertise across the hill state, from Baddi's industrial belt to orchard country.</p>
          <a class="btn btn-ghost" href="/explore?category=${encodeURIComponent('Manufacturers & factories')}">Explore manufacturers</a></div>
        <div class="pillars">
          <div class="pillar"><h3>Reviewed before publishing</h3><p>New submissions go through a review process.</p></div>
          <div class="pillar"><h3>Owned by real people</h3><p>Owners can manage their own business information.</p></div>
          <div class="pillar"><h3>Clear, honest information</h3><p>No invented ratings or unearned verification badges.</p></div>
        </div></div></section>

      <section class="cta"><div class="eyebrow" style="color:#ffe3d9">Your business. Your next chapter.</div>
        <h2>Be found by the people looking for <em>you.</em></h2>
        <a class="btn" href="/dashboard">Add your business for free</a>
        <p>No payment required. Every submission is reviewed.</p></section>`;
    bindSearch($app);
    try {
      const { items } = await api('/businesses/latest');
      const box = document.getElementById('latest');
      if (!box) return;
      box.innerHTML = items.length
        ? `<div class="list-grid">${items.map(businessCard).join('')}</div>`
        : `<div class="empty"><h3>Your next local connection belongs here.</h3><p>Our directory is growing. Be one of the first businesses to join.</p><a class="btn btn-apple" href="/dashboard">Add a free listing</a></div>`;
    } catch { /* leave skeleton off */
      const box = document.getElementById('latest');
      if (box) box.innerHTML = '<div class="empty"><p>Listings could not be loaded right now.</p></div>';
    }
  }

  async function pageExplore() {
    // Read the filters from the URL so a search can be bookmarked or shared.
    const searchParameters = new URLSearchParams(location.search);
    const keyword = searchParameters.get('q') || '';
    const category = searchParameters.get('category') || '';
    const city = searchParameters.get('city') || '';
    const pageNumber = Math.max(1, parseInt(searchParameters.get('page') || '1', 10) || 1);
    setMeta('Explore businesses', 'Browse approved businesses, manufacturers and local services across Himachal Pradesh.');
    $app.innerHTML = `<div class="page"><div class="wrap">
      <div class="page-head"><h1>Explore businesses</h1><p>Search approved listings across Himachal Pradesh.</p></div>
      <div class="filters">${searchForm({ q: keyword, category, city })}</div>
      <div id="results"><div class="list-grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
    </div></div>`;
    bindSearch($app);
    const token = renderToken;
    try {
      const requestParameters = new URLSearchParams({
        q: keyword, category, city, page: pageNumber,
      });
      const results = await api('/businesses?' + requestParameters);
      if (token !== renderToken) return;
      const box = document.getElementById('results');
      if (!results.items.length) {
        box.innerHTML = `<div class="empty"><h3>No businesses found</h3><p>${keyword || category || city ? 'Try a different keyword, or remove a filter.' : 'No listings have been published yet. Be the first to add one.'}</p><a class="btn btn-apple" href="/dashboard">Add a free listing</a></div>`;
        return;
      }
      const pageLink = (targetPage) => {
        const pageParameters = new URLSearchParams(location.search);
        pageParameters.set('page', targetPage);
        return '/explore?' + pageParameters;
      };
      box.innerHTML = `<div class="result-meta">${results.total} ${results.total === 1 ? 'business' : 'businesses'} found</div>
        <div class="list-grid">${results.items.map(businessCard).join('')}</div>
        ${results.pages > 1 ? `<div class="pager">
          ${results.page > 1 ? `<a class="btn btn-ghost btn-sm" href="${pageLink(results.page - 1)}">Previous</a>` : ''}
          <span>Page ${results.page} of ${results.pages}</span>
          ${results.page < results.pages ? `<a class="btn btn-ghost btn-sm" href="${pageLink(results.page + 1)}">Next</a>` : ''}</div>` : ''}`;
    } catch (error) {
      const box = document.getElementById('results');
      if (box) box.innerHTML = `<div class="empty"><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  async function pageBusiness(slug) {
    $app.innerHTML = `<div class="page"><div class="wrap"><div class="skeleton"></div></div></div>`;
    const token = renderToken;
    try {
      const { item: business } = await api('/businesses/' + encodeURIComponent(slug));
      if (token !== renderToken) return;
      setMeta(business.name, snippet(business.description, 155));
      const safeWebsite = /^https?:\/\//.test(business.website) ? business.website : '';
      $app.innerHTML = `<div class="page"><div class="wrap">
        <div class="crumb"><a href="/explore">Explore businesses</a> / ${escapeHtml(business.city)}</div>
        <div class="detail"><div>
          <div class="tags"><span class="tag">${escapeHtml(business.category)}</span><span class="tag tag-city">${escapeHtml(business.city)}</span>
            ${business.status && business.status !== 'approved' ? `<span class="badge badge-${escapeHtml(business.status)}">${business.status === 'pending' ? 'Pending review — only you can see this' : escapeHtml(business.status)}</span>` : ''}</div>
          <h1>${escapeHtml(business.name)}</h1>
          <p>${escapeHtml(business.description).replace(/\n/g, '<br>')}</p></div>
        <aside class="card"><h3>Contact</h3><dl>
          ${business.address ? `<dt>Address</dt><dd>${escapeHtml(business.address)}</dd>` : ''}
          <dt>Phone</dt><dd>${business.phone ? `<a class="btn btn-sm" href="tel:${escapeHtml(business.phone.replace(/[^+0-9]/g, ''))}">Call ${escapeHtml(business.phone)}</a>` : '<span class="contact-missing">Not provided</span>'}</dd>
          ${business.email ? `<dt>Email</dt><dd><a href="mailto:${escapeHtml(business.email)}">${escapeHtml(business.email)}</a></dd>` : ''}
          ${safeWebsite ? `<dt>Website</dt><dd><a href="${escapeHtml(safeWebsite)}" target="_blank" rel="noopener noreferrer nofollow">${escapeHtml(safeWebsite.replace(/^https?:\/\//, ''))}</a></dd>` : ''}
          <dt>Listed</dt><dd>${escapeHtml(formatDate(business.created_at))}</dd></dl></aside></div></div></div>`;
    } catch (error) {
      if (token !== renderToken) return;
      setMeta('Not found');
      $app.innerHTML = `<div class="page"><div class="wrap"><div class="empty"><h3>Business not found</h3><p>This listing may have been removed or is still awaiting review.</p><a class="btn" href="/explore">Explore businesses</a></div></div></div>`;
    }
  }

  function pageAbout() {
    setMeta('About us', 'How Himachal Directory works, how listings are reviewed, and how to enquire about advertising.');
    $app.innerHTML = `<div class="page"><div class="wrap">
      <div class="page-head"><div class="eyebrow">About the directory</div><h1>Local knowledge. <em>Better connections.</em></h1>
        <p>Himachal Directory helps people find businesses, manufacturers and services across Himachal Pradesh, and helps owners be found.</p></div>
      <div class="prose">
        <h2>How listings work</h2>
        <ul><li>Any business owner can create a free account and submit a listing.</li>
          <li>Every submission is reviewed before it appears publicly.</li>
          <li>Owners can edit or remove their own listings at any time. Edited listings are reviewed again.</li>
          <li>We do not show invented ratings, and we do not award verification badges we have not earned.</li></ul>
        <h2 id="advertise">Business visibility and advertising</h2>
        <p>Advertising space is not currently bookable. Leave your details and we will get in touch if that changes.</p>
      </div>
      <form class="card prose" id="enquiry-form" novalidate>
        <div class="form-msg" hidden></div>
        <div class="grid-2">${field('name', 'Your name', '', { required: true, autocomplete: 'name', maxlength: 80 })}${field('email', 'Email', '', { type: 'email', required: true, autocomplete: 'email' })}</div>
        ${field('message', 'Message', '', { type: 'textarea', required: true, maxlength: 1500 })}
        <button class="btn btn-apple" type="submit">Send enquiry</button>
      </form></div></div>`;
    const form = document.getElementById('enquiry-form');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitForm(form, async () => {
        await api('/enquiry', { method: 'POST', body: formValues(form) });
        form.reset();
        showFormOk(form, 'Thanks. Your enquiry has been sent.');
      });
    });
  }

  function pagePrivacy() {
    setMeta('Privacy & guidelines', 'How Himachal Directory handles your data and what we expect from listings.');
    $app.innerHTML = `<div class="page"><div class="wrap"><div class="page-head"><h1>Privacy &amp; guidelines</h1></div>
      <div class="prose">
        <h2>What we collect</h2>
        <p>When you register we store your name, email address and a securely hashed password. When you submit a listing we store the details you enter. Enquiries from the About page are stored so we can reply.</p>
        <h2>What is public</h2>
        <p>Once a listing is approved, its name, category, city, description and any phone number, email, address and website you entered are visible to everyone. Only enter contact details you want customers to see. Your account email is never shown unless you add it to a listing.</p>
        <h2>Cookies</h2>
        <p>We use a single sign-in cookie so you stay logged in. We do not use advertising or tracking cookies.</p>
        <h2>Listing guidelines</h2>
        <ul><li>List a real business that you own or are authorised to represent.</li>
          <li>Describe what you actually offer. Do not make claims you cannot support.</li>
          <li>Do not post offensive, misleading or unlawful content.</li>
          <li>Listings that break these rules may be rejected or removed.</li></ul>
        <h2>Your choices</h2>
        <p>You can edit or delete your listings from your dashboard at any time.</p>
      </div></div></div>`;
  }

  function pageLogin() {
    setMeta('Sign in', '');
    if (state.user) return navigate('/dashboard', true);
    const next = new URLSearchParams(location.search).get('next');
    $app.innerHTML = `<div class="page"><div class="wrap"><div class="auth card">
      <div class="tabs" role="tablist">
        <button role="tab" id="tab-in" aria-selected="true" aria-controls="panel">Sign in</button>
        <button role="tab" id="tab-up" aria-selected="false" aria-controls="panel">Create account</button></div>
      <form id="auth-form" novalidate></form></div></div></div>`;
    const form = document.getElementById('auth-form');
    let mode = 'in';
    function draw() {
      document.getElementById('tab-in').setAttribute('aria-selected', mode === 'in');
      document.getElementById('tab-up').setAttribute('aria-selected', mode === 'up');
      form.innerHTML = `<div class="form-msg" hidden></div>
        ${mode === 'up' ? field('name', 'Your name', '', { required: true, autocomplete: 'name', maxlength: 80 }) : ''}
        ${field('email', 'Email', '', { type: 'email', required: true, autocomplete: 'email' })}
        ${field('password', 'Password', '', { type: 'password', required: true, autocomplete: mode === 'up' ? 'new-password' : 'current-password', hint: mode === 'up' ? 'At least 8 characters.' : '' })}
        <button class="btn btn-apple" type="submit" style="width:100%">${mode === 'up' ? 'Create account' : 'Sign in'}</button>`;
    }
    document.getElementById('tab-in').addEventListener('click', () => { mode = 'in'; draw(); });
    document.getElementById('tab-up').addEventListener('click', () => { mode = 'up'; draw(); });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitForm(form, async () => {
        const { user } = await api(mode === 'up' ? '/register' : '/login', { method: 'POST', body: formValues(form) });
        state.user = user; renderHeader();
        navigate(next && next.startsWith('/') && !next.startsWith('//') ? next : '/dashboard', true);
      });
    });
    draw();
  }

  async function pageDashboard() {
    setMeta('Your dashboard', '');
    if (!state.user) {
      $app.innerHTML = `<div class="page"><div class="wrap"><div class="auth card"><h2>Add your business for free</h2>
        <p>Sign in or create a free account to submit and manage your listings. No payment required.</p>
        <a class="btn btn-apple" href="/login?next=/dashboard">Sign in or register</a></div></div></div>`;
      return;
    }
    $app.innerHTML = `<div class="page"><div class="wrap">
      <div class="page-head"><h1>Your dashboard</h1><p>Welcome, ${escapeHtml(state.user.name)}. Submit a business or manage the ones you have added.
        ${state.user.is_admin ? '<a class="link-arrow" href="/admin">Open admin review</a>' : ''}</p></div>
      <div class="dash-grid"><section class="card"><h2 id="form-title">Add a business</h2><form id="biz-form" novalidate></form></section>
      <section class="card"><h2>Your listings</h2><div id="my-list"><div class="skeleton"></div></div></section></div></div></div>`;

    let userListings = [];
    let listingBeingEdited = null;
    const form = document.getElementById('biz-form');

    function renderListingForm() {
      const listing = listingBeingEdited || {};
      document.getElementById('form-title').textContent = listingBeingEdited ? 'Edit business' : 'Add a business';
      form.innerHTML = `<div class="form-msg" hidden></div>
        ${field('name', 'Business name', listing.name, { required: true, maxlength: 100 })}
        <div class="grid-2">${field('category', 'Category', listing.category, { type: 'select', options: state.meta.categories, placeholder: 'Choose a category', required: true })}
          ${field('city', 'City', listing.city, { type: 'select', options: state.meta.cities, placeholder: 'Choose a city', required: true })}</div>
        ${field('description', 'Description', listing.description, { type: 'textarea', required: true, maxlength: 1200, hint: 'Tell customers what you offer. 20 to 1,200 characters.' })}
        ${field('address', 'Address', listing.address, { maxlength: 200 })}
        <div class="grid-2">${field('phone', 'Phone number', listing.phone, { type: 'tel', required: true, maxlength: 20, hint: 'Required. Customers can call this number after your listing is approved.' })}${field('email', 'Business email', listing.email, { type: 'email', hint: 'Optional. Shown publicly once approved.' })}</div>
        ${field('website', 'Website', listing.website, { type: 'url', hint: 'Start with https://' })}
        <button class="btn btn-apple" type="submit">${listingBeingEdited ? 'Save and resubmit for review' : 'Submit for review'}</button>
        ${listingBeingEdited ? '<button class="btn btn-ghost" type="button" data-cancel style="margin-left:8px">Cancel</button>' : ''}`;
      const cancelButton = form.querySelector('[data-cancel]');
      if (cancelButton) {
        cancelButton.addEventListener('click', () => {
          listingBeingEdited = null;
          renderListingForm();
        });
      }
    }

    function renderUserListings() {
      const listingsContainer = document.getElementById('my-list');
      if (!userListings.length) {
        listingsContainer.innerHTML = '<p>You have not added a business yet. Use the form to submit your first listing. Every submission is reviewed.</p>';
        return;
      }
      const statusLabels = { pending: 'Pending review', approved: 'Live', rejected: 'Needs changes' };
      listingsContainer.innerHTML = userListings.map((listing) => `<div class="row-item"><div>
          <h3>${listing.status === 'approved' ? `<a href="/business/${escapeHtml(listing.slug)}">${escapeHtml(listing.name)}</a>` : escapeHtml(listing.name)}</h3>
          <span class="badge badge-${escapeHtml(listing.status)}">${statusLabels[listing.status] || escapeHtml(listing.status)}</span>
          <p>${escapeHtml(listing.category)} · ${escapeHtml(listing.city)}</p>
          ${listing.status === 'rejected' && listing.reject_reason ? `<div class="reason-note">Reviewer note: ${escapeHtml(listing.reject_reason)}</div>` : ''}</div>
          <div class="row-actions"><button class="btn btn-ghost btn-sm" data-edit="${listing.id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-del="${listing.id}">Delete</button></div></div>`).join('');
    }

    async function loadUserListings() {
      try { userListings = (await api('/my/businesses')).items; }
      catch (err) { if (err.status === 401) { state.user = null; renderHeader(); return navigate('/login?next=/dashboard', true); } }
      renderUserListings();
    }

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitForm(form, async () => {
        const listingData = formValues(form);
        if (listingBeingEdited) {
          await api('/my/businesses/' + listingBeingEdited.id, {
            method: 'PUT',
            body: listingData,
          });
        } else {
          await api('/my/businesses', { method: 'POST', body: listingData });
        }
        const wasEditing = !!listingBeingEdited;
        listingBeingEdited = null;
        await loadUserListings();
        renderListingForm();
        showFormOk(form, wasEditing ? 'Saved. Your listing is back in review.' : 'Submitted. We will review your listing before it appears publicly.');
      });
    });
    document.getElementById('my-list').addEventListener('click', async (event) => {
      const editButton = event.target.closest('[data-edit]');
      const deleteButton = event.target.closest('[data-del]');
      if (editButton) {
        listingBeingEdited = userListings.find(
          (listing) => listing.id === Number(editButton.dataset.edit)
        );
        renderListingForm();
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      if (deleteButton && confirm('Delete this listing? This cannot be undone.')) {
        try {
          await api('/my/businesses/' + deleteButton.dataset.del, { method: 'DELETE' });
          listingBeingEdited = null;
          await loadUserListings();
          renderListingForm();
        } catch (error) {
          alert(error.message);
        }
      }
    });
    renderListingForm();
    await loadUserListings();
  }

  async function pageAdmin() {
    setMeta('Admin review', '');
    if (!state.user || !state.user.is_admin) {
      $app.innerHTML = `<div class="page"><div class="wrap"><div class="empty"><h3>Admins only</h3><p>Sign in with an admin account to review listings.</p><a class="btn" href="/login?next=/admin">Sign in</a></div></div></div>`;
      return;
    }
    let selectedTab = 'pending';
    $app.innerHTML = `<div class="page"><div class="wrap"><div class="page-head"><h1>Admin review</h1><p>Approve or reject submitted listings. Rejections need a short reason for the owner.</p></div>
      <div class="tabs" role="tablist" id="admin-tabs">
        <button role="tab" data-tab="pending">Pending</button><button role="tab" data-tab="approved">Approved</button>
        <button role="tab" data-tab="rejected">Rejected</button><button role="tab" data-tab="enquiries">Enquiries</button></div>
      <div class="card" id="admin-body"></div></div></div>`;
    const adminContent = document.getElementById('admin-body');

    async function renderAdminTab() {
      document.querySelectorAll('#admin-tabs button').forEach((tabButton) => {
        tabButton.setAttribute('aria-selected', tabButton.dataset.tab === selectedTab);
      });
      adminContent.innerHTML = '<div class="skeleton"></div>';
      try {
        if (selectedTab === 'enquiries') {
          const { items: enquiries } = await api('/admin/enquiries');
          adminContent.innerHTML = enquiries.length
            ? enquiries.map((enquiry) => `<div class="row-item"><div><h3>${escapeHtml(enquiry.name)}</h3>
              <a href="mailto:${escapeHtml(enquiry.email)}">${escapeHtml(enquiry.email)}</a><p>${escapeHtml(enquiry.message)}</p></div><span>${escapeHtml(formatDate(enquiry.created_at))}</span></div>`).join('')
            : '<p>No enquiries yet.</p>';
          return;
        }
        const { items: businessRecords, counts: listingCounts } = await api('/admin/businesses?status=' + selectedTab);
        document.querySelectorAll('#admin-tabs button[data-tab]').forEach((tabButton) => {
          if (listingCounts[tabButton.dataset.tab] !== undefined) {
            tabButton.textContent = `${tabButton.dataset.tab[0].toUpperCase()}${tabButton.dataset.tab.slice(1)} (${listingCounts[tabButton.dataset.tab]})`;
          }
        });
        adminContent.innerHTML = businessRecords.length ? businessRecords.map((business) => `<div class="row-item"><div>
            <h3>${escapeHtml(business.name)}</h3><div class="tags"><span class="tag">${escapeHtml(business.category)}</span><span class="tag tag-city">${escapeHtml(business.city)}</span></div>
            <p>${escapeHtml(business.description)}</p>
            <p>${[business.phone, business.email, business.website, business.address].filter(Boolean).map(escapeHtml).join(' · ')}</p>
            <p>Owner: ${escapeHtml(business.owner_name)} (${escapeHtml(business.owner_email)})</p>
            ${business.reject_reason ? `<div class="reason-note">Reason: ${escapeHtml(business.reject_reason)}</div>` : ''}</div>
            <div class="row-actions">
              ${selectedTab !== 'approved' ? `<button class="btn btn-sm" data-approve="${business.id}">Approve</button>` : ''}
              ${selectedTab !== 'rejected' ? `<button class="btn btn-danger btn-sm" data-reject="${business.id}">Reject</button>` : ''}
            </div></div>`).join('') : `<p>Nothing in ${escapeHtml(selectedTab)}.</p>`;
      } catch (error) { adminContent.innerHTML = `<p>${escapeHtml(error.message)}</p>`; }
    }

    document.getElementById('admin-tabs').addEventListener('click', (event) => {
      const selectedButton = event.target.closest('[data-tab]');
      if (selectedButton) {
        selectedTab = selectedButton.dataset.tab;
        renderAdminTab();
      }
    });
    adminContent.addEventListener('click', async (event) => {
      const approveButton = event.target.closest('[data-approve]');
      const rejectButton = event.target.closest('[data-reject]');
      try {
        if (approveButton) {
          await api(`/admin/businesses/${approveButton.dataset.approve}/review`, {
            method: 'POST',
            body: { action: 'approve' },
          });
          renderAdminTab();
        }
        if (rejectButton) {
          const reason = prompt('Reason for rejection (shown to the owner):');
          if (reason === null) return;
          await api(`/admin/businesses/${rejectButton.dataset.reject}/review`, {
            method: 'POST',
            body: { action: 'reject', reason },
          });
          renderAdminTab();
        }
      } catch (error) { alert(error.message); }
    });
    renderAdminTab();
  }

  function pageNotFound() {
    setMeta('Page not found', '');
    $app.innerHTML = `<div class="page"><div class="wrap"><div class="empty"><h3>Page not found</h3><p>We could not find that page.</p><a class="btn" href="/">Back to home</a></div></div></div>`;
  }

  /* ---------- page router ---------- */
  // Render the page that matches the current URL.
  async function render() {
    renderToken += 1;
    const path = location.pathname.replace(/\/+$/, '') || '/';
    let businessPageMatch;
    if (path === '/') await pageHome();
    else if (path === '/explore') await pageExplore();
    else if ((businessPageMatch = path.match(/^\/business\/([a-z0-9-]+)$/))) {
      await pageBusiness(businessPageMatch[1]);
    }
    else if (path === '/about') pageAbout();
    else if (path === '/privacy') pagePrivacy();
    else if (path === '/login') pageLogin();
    else if (path === '/dashboard') await pageDashboard();
    else if (path === '/admin') await pageAdmin();
    else pageNotFound();

    if (location.hash) {
      const el = document.getElementById(location.hash.slice(1));
      if (el) { el.scrollIntoView(); return; }
    }
    window.scrollTo(0, 0);
    $app.focus({ preventScroll: true });
  }

  // Handle links within the site without asking the browser to reload the page.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a');
    if (!a || a.target || a.hasAttribute('download') || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const href = a.getAttribute('href');
    if (!href || href === '#' || !href.startsWith('/') || href.startsWith('//')) return;
    e.preventDefault();
    navigate(href);
  });
  window.addEventListener('popstate', render);

  // Load shared data first, then show the page the visitor requested.
  (async function boot() {
    document.getElementById('year').textContent = new Date().getFullYear();
    try { state.meta = await api('/meta'); } catch { /* use empty */ }
    try { state.user = (await api('/me')).user; } catch { /* signed out */ }
    renderHeader();
    render();
  })();
})();
