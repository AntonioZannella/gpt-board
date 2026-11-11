// Product dialogs use text nodes for all notebook and AI supplied content.
// The drawing engine is supplied through small callbacks, not global DOM state.
import { pageNumbers } from './file-options.js';
import { pageAction, hiddenRecord, replacementFrames } from './pages.js';

export async function request(path, body, signal) {
  const response = await fetch(
    path,
    body === undefined
      ? { signal }
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal,
        },
  );
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Request failed');
  return data;
}
export function download(value, name, type = 'application/json') {
  const blob =
    value instanceof Blob
      ? value
      : new Blob([typeof value === 'string' ? value : JSON.stringify(value, null, 2)], { type });
  const link = document.createElement('a'),
    url = URL.createObjectURL(blob);
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
export function installPanels(api) {
  const dialog = document.getElementById('manage-dialog'),
    title = document.getElementById('manage-title'),
    body = document.getElementById('manage-body');
  let cancelled = false,
    abort = null,
    activeImport = null, activeExport = null;
  const el = (tag, text, attrs = {}) =>
    Object.assign(document.createElement(tag), { textContent: text, ...attrs });
  function button(text, action) {
    const b = el('button', text, { type: 'button' });
    b.onclick = async () => {
      b.disabled = true;
      try {
        await action();
      } catch (e) {
        api.announce(e.message);
        message(e.message);
      } finally {
        b.disabled = false;
      }
    };
    return b;
  }
  function message(text) {
    const p = el('p', text);
    p.setAttribute('role', 'status');
    body.append(p);
  }
  function open(name) {
    title.textContent = name;
    body.replaceChildren();
    if (!dialog.open) dialog.showModal();
  }
  function state() {
    const s = api.state();
    if (!s.store) throw Error('Open a notebook first');
    return s;
  }
  dialog.addEventListener('close', () => {
    cancelled = true;
    abort?.abort();
    if (activeImport)
      request('/api/import', { action: 'cancel', job: activeImport }).catch(() => {});
    activeImport = null;
    if (activeExport) request('/api/export-pdf', {action:'cancel', job:activeExport}).catch(()=>{});
    activeExport = null;
  });
  document.getElementById('close-manage').onclick = () => dialog.close();

  async function library() {
    open('Notebook library');
    const s = api.state();
    await api.thumbnail().catch(() => {});
    const data = await request('/api/notebooks?all=1&previews=1&kind=' + s.kind);
    const search = el('input', '', {
      type: 'search',
      placeholder: 'Search titles',
      ariaLabel: 'Search notebook titles',
    });
    const list = el('div', '');
    const render = () => {
      list.replaceChildren();
      for (const book of data.notebooks.filter((b) =>
        b.name.toLowerCase().includes(search.value.toLowerCase()),
      )) {
        const row = el('section', '', { className: 'manage-row' });
        row.append(el('h3', book.name), el('p', book.status || 'active'));
        if (book.thumbnail)
          row.append(
            el('img', '', {
              src: '/api/thumbnail?notebook=' + encodeURIComponent(book.id),
              alt: 'Last saved preview of ' + book.name,
              width: 100,
            }),
          );
        row.append(
          button('Open', () => api.navigate(book.id)),
          button('Rename', async () => {
            const name = prompt('Notebook name', book.name);
            if (!name) return;
            await request('/api/notebooks', { action: 'rename', notebook: book.id, name });
            await library();
          }),
        );
        row.append(button('Duplicate notebook', async () => {
          if (book.id === s.book) await api.save();
          const document = await request('/api/files?notebook='+encodeURIComponent(book.id));
          document.notebook.name = (book.name.slice(0,72)+' (copy)').slice(0,80);
          await request('/api/files', {document});
          await library();
        }));
        for (const action of ['up','down']) row.append(button('Move '+action, async()=>{
          await request('/api/notebooks',{action,notebook:book.id}); await library();
        }));
        for (const action of book.status && book.status !== 'active'
          ? ['restore']
          : ['archive', 'trash'])
          row.append(
            button(action[0].toUpperCase() + action.slice(1), async () => {
              if (book.id === s.book) await api.save();
              await request('/api/notebooks', { action, notebook: book.id });
              await library();
            }),
          );
        list.append(row);
      }
    };
    search.oninput = render;
    body.append(search, list);
    render();
    search.focus();
    message(
      'Archive and Trash preserve notebook files and IDs. Restore returns them to the picker.',
    );
  }
  function pages() {
    open('Manage pages');
    const s = state();
    const search = el('input', '', {
        type: 'search',
        placeholder: 'Search page labels',
        ariaLabel: 'Search page labels',
      }),
      list = el('div', '');
    const render = () => {
      list.replaceChildren();
      const all = [
        ...s.frames,
        ...Object.values(s.store).filter((r) => r.type === 'frame' && r.meta?.trashed),
      ];
      for (const frame of all.filter((f) =>
        f.props.name.toLowerCase().includes(search.value.toLowerCase()),
      )) {
        const row = el('section', '', { className: 'manage-row' });
        row.append(el('h3', frame.props.name));
        row.append(el('small', frame.id + (frame.meta?.trashed ? ' · In trash' : '')));
        for (const action of frame.meta?.trashed
          ? ['restore']
          : ['open', 'rename', 'duplicate', 'up', 'down', 'trash'])
          row.append(
            button(action[0].toUpperCase() + action.slice(1), async () => {
              if (action === 'open') {
                api.showPage(frame.id);
                dialog.close();
                return;
              }
              const value = action === 'rename' ? prompt('Page name', frame.props.name) : null;
              if (action === 'rename' && !value) return;
              let id;
              api.change(
                () => (id = pageAction(s.store, s.frames, frame.id, action, value, api.uid)),
              );
              await api.save();
              if (action !== 'trash') api.showPage(id);
              pages();
            }),
          );
        list.append(row);
      }
    };
    search.oninput = render;
    body.append(search, list);
    render();
    search.focus();
    message('Page trash is reversible. IDs and historical review evidence stay unchanged.');
  }
  async function ai() {
    open('AI target and annotations');
    const s = state(),
      info = await request('/api/ai?notebook=' + encodeURIComponent(s.book));
    message(
      info.target
        ? `Selected target: ${info.target.notebook} / ${info.target.frame} / tab ${info.target.tab.slice(0, 8)}`
        : 'No target selected. Explicit notebook/page tool arguments are required.',
    );
    body.append(
      button('Use this tab and current page as AI target', async () => {
        await api.save();
        await request('/api/ai', {
          action: 'target',
          notebook: s.book,
          tab: s.tab,
          frame: s.current?.id || 'whiteboard',
        });
        await ai();
      }),
    );
    for (const op of [...info.operations].reverse()) {
      const row = el('section', '', { className: 'manage-row' });
      row.append(el('h3', op.status.replaceAll('_', ' ')), el('code', op.operation_id));
      const content = el(
        'pre',
        JSON.stringify(op.request || { before: op.before, after: op.after }, null, 2),
      );
      row.append(content);
      if (op.status === 'approval_required')
        row.append(
          button('Approve these exact changes', async () => {
            await api.save();
            await request('/api/ai', {
              action: 'approve',
              notebook: s.book,
              operation_id: op.operation_id,
            });
            await api.reconcile();
            await ai();
          }),
        );
      if (op.status === 'committed')
        row.append(
          button('Undo this AI edit', async () => {
            await api.save();
            await request('/api/ai', {
              action: 'undo',
              notebook: s.book,
              operation_id: op.operation_id,
            });
            await api.reconcile();
            await ai();
          }),
        );
      body.append(row);
    }
    const notes = Object.values(s.store).filter(
      (r) => (r.meta?.ai || ['text', 'note'].includes(r.type)) && !hiddenRecord(r, s.store),
    );
    body.append(el('h3', 'Readable annotations and typed notes'));
    for (const note of notes) {
      const row = el('section', '', { className: 'manage-row' });
      const input = el('textarea', '', {
        value: api.plain(note.props.richText),
        ariaLabel: note.meta?.ai ? 'Annotation text' : 'Typed note text',
        rows: 4,
      });
      row.append(
        el(
          'p',
          `${note.meta?.ai ? 'AI annotation' : 'Your typed note'} · ${s.store[note.parentId]?.props?.name || 'Whiteboard'}`,
        ),
        el('code', note.id),
        input,
        button(note.meta?.ai ? 'Save annotation text' : 'Save typed note', async () => {
          api.change(() => {
            note.props.richText = api.rich(input.value);
          });
          await api.save();
        }),
      );
      body.append(row);
    }
    message('Typed notes support Unicode math such as x², √, π and ∫; no LaTeX typesetting.');
    body.append(button('View canonical learning review history', () => reviews()));
    body.append(
      button('Add a typed note on this page', async () => {
        const text = prompt('Note text');
        if (!text) return;
        api.addNote(text);
        await api.save();
        await ai();
      }),
    );
  }
  async function reviews(course, offset = 0) {
    open('Learning review history');
    if (!course) {
      const info = await request('/api/reviews');
      if (!info.enabled) {
        message('Review logging is not configured. Drawing does not require it.');
        return;
      }
      for (const name of info.courses) body.append(button(name, () => reviews(name)));
      return;
    }
    const info = await request(
      '/api/reviews?course=' + encodeURIComponent(course) + '&offset=' + offset,
    );
    message(info.notice);
    message(
      `${course}: showing ${info.total ? offset + 1 : 0}–${Math.min(offset + 100, info.total)} of ${info.total} historical checks.`,
    );
    for (const review of info.reviews) {
      const row = el('section', '', { className: 'manage-row' });
      row.append(
        el('h3', `${review.result} · ${review.section} / ${review.page} / ${review.problem}`),
        el(
          'p',
          `Review ${review.review_id || review.id || 'unknown'} · ${review.ts || review.created || 'date not recorded'}`,
        ),
        el('p', `Original response: ${review.original_response || 'Not recorded'}`),
        el(
          'p',
          `Assistance: ${review.assistance || 'unknown'} · Read confidence: ${review.read_confidence || 'unknown'}`,
        ),
      );
      if (review.supersedes)
        row.append(
          el('p', `Corrects prior review: ${review.supersedes.review_id || review.supersedes}`),
        );
      if (review.resolves?.length)
        row.append(
          el(
            'p',
            `Resolves: ${review.resolves.map((link) => `${link.review_id || link}${link.error_id ? ' / ' + link.error_id : ''}`).join(', ')}`,
          ),
        );
      const details = el('details', '');
      details.append(
        el('summary', 'Full recorded reasoning, errors and correction links'),
        el('pre', JSON.stringify(review, null, 2)),
      );
      row.append(details);
      body.append(row);
    }
    if (offset)
      body.append(button('Newer checks', () => reviews(course, Math.max(0, offset - 100))));
    if (offset + 100 < info.total)
      body.append(button('Older checks', () => reviews(course, offset + 100)));
  }
  function recovery() {
    open('Recovery and keyboard help');
    const s = state();
    message(
      'Recovery is for interrupted connections after opening the app online. Starting with no server connection is not supported. Browser clearing or storage eviction can remove local journals.',
    );
    body.append(
      button('Download unsent edits and preserved conflicts', () =>
        download(api.recovery(), 'gpt-board-recovery.json'),
      ),
    );
    body.append(
      button('Download a separate notebook with recovered local versions', () =>
        download(api.recoveryNotebook(), 'recovered-local-copy.gpt-board.json'),
      ),
    );
    message(
      'Open Files to preview and import that recovered copy under a new notebook ID. The original notebook stays unchanged.',
    );
    message(
      'Conflicting journals remain stored until you have inspected/exported them. They are never silently discarded.',
    );
    message(
      'Ctrl/⌘ Z: Undo · Ctrl/⌘ Shift Z: Redo · Page Up/Down: change page · Delete: delete selection · Escape: close dialogs. Select writing with the Select tool.',
    );
    message(
      `Notebook: ${s.book}. For damaged server files: stop writers, back up data, and use board.py recover-index --dry-run or board.py backups / restore. See docs/RECOVERY.md.`,
    );
  }
  async function files(sharedFile) {
    open('Import, export and print');
    const s = state();
    cancelled = false;
    const annotations = el('input', '', { type: 'checkbox', checked: true, id: 'include-ai' }),
      label = el('label', ' Include AI annotations');
    label.prepend(annotations);
    body.append(label);
    body.append(
      button('Download notebook JSON', async () => {
        await api.save();
        download(
          await request('/api/files?notebook=' + encodeURIComponent(s.book)),
          s.book + '.gpt-board.json',
        );
      }),
    );
    body.append(
      button('Export current page PNG (print resolution)', async () => {
        const { png } = await api.exportPage(s.current, annotations.checked, 3000);
        const link = el('a', '', { href: png, download: s.book + '.png' });
        link.click();
      }),
    );
    const range = el('input', '', {value:'all', ariaLabel:'Pages to export', placeholder:'all or 1-5, 8, 12-14'});
    body.append(el('h3', 'PDF with handwriting'), el('p', 'Choose all pages or a range. Pages are rendered one at a time to keep iPad memory use bounded.'), range);
    body.append(button('Export selected pages PDF', async () => {
      await api.save();
      cancelled = false;
      abort = new AbortController();
      const exportRevision = api.state().baseRev;
      const list = s.frames.length ? s.frames : [null];
      const selected = pageNumbers(range.value, list.length);
      const progress = el('p','Preparing export…', {role:'status'}); body.append(progress);
      const job = await request('/api/export-pdf', {action:'begin'}, abort.signal);
      activeExport = job.id;
      try {
        for (let i=0; i<selected.length; i++) {
          if (cancelled) return;
          progress.textContent = `Exporting page ${i+1} of ${selected.length}…`;
          const page = list[selected[i]];
          const result = await api.exportPage(page, annotations.checked, 2400);
          if (api.state().baseRev !== exportRevision) throw Error('The notebook changed during export. Retry to export a consistent version.');
          await request('/api/export-pdf', {action:'page', job:job.id, page:i, png:result.png,
            w:page?.props.w || 1000, h:page?.props.h || 1000}, abort.signal);
        }
        const response = await fetch('/api/export-pdf', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({action:'finish',job:job.id,count:selected.length}),signal:abort.signal});
        if (!response.ok) throw Error((await response.json()).error || 'PDF export failed');
        download(await response.blob(), s.book+'.pdf');
        progress.textContent = 'PDF ready, including handwriting. Save or share it from the download sheet.';
      } finally {
        request('/api/export-pdf',{action:'cancel',job:job.id}).catch(()=>{});
        if (activeExport === job.id) activeExport = null;
      }
    }));
    const picker = el('input', '', {
      type: 'file',
      accept: '.json,.pdf,.png,.jpg,.jpeg',
      ariaLabel: 'Choose notebook, PDF or image to import',
    });
    body.append(picker);
    const chooseFile = async (file) => {
      try {
        cancelled = false;
        if (activeImport) await request('/api/import', {action:'cancel',job:activeImport}).catch(()=>{});
        activeImport = null;
        if (!file) return;
        const isNotebook = file.name.toLowerCase().endsWith('.json');
        const maxMiB = isNotebook ? 64 : 16;
        if (file.size > maxMiB * 1024 * 1024) throw Error(`Choose ${isNotebook ? 'a notebook backup' : 'a PDF or image'} up to ${maxMiB} MiB.`);
        await api.save();
        if (file.name.toLowerCase().endsWith('.json')) {
          const document = JSON.parse(await file.text()),
            preview = await request('/api/files', { document, preview: true });
          message(
            `Preview: ${preview.name}, ${preview.pages} pages, ${preview.strokes} strokes. Imports get a new notebook ID.`,
          );
          body.append(
            button('Import this notebook', async () => {
              const result = await request('/api/files', { document });
              await api.navigate(result.notebook.id, preview.kind === 'whiteboard');
            }),
          );
          return;
        }
        const encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(Error('Could not read this file.'));
          reader.readAsDataURL(file);
        });
        abort = new AbortController();
        const job = await request(
          '/api/import',
          { action: 'prepare', name: file.name, data: encoded },
          abort.signal,
        );
        activeImport = job.id;
        const destination = el('select', '', {ariaLabel:'Import destination'});
        destination.append(new Option('New notebook', 'new'), new Option('Append to this notebook', 'append'), new Option('Replace PDF backgrounds, keep handwriting', 'replace'));
        const name = el('input','',{value:file.name.replace(/\.[^.]+$/, '').slice(0,80),ariaLabel:'New notebook name',maxLength:80});
        const mapping = el('div','');
        const selects = [];
        const backgrounds = replacementFrames(s.frames, s.store);
        for (let i=0;i<job.pages;i++) {
          const select = el('select','',{ariaLabel:`Replace destination for PDF page ${i+1}`});
          select.append(new Option('Choose existing page…',''));
          for (const frame of backgrounds) select.append(new Option(frame.props.name,frame.id));
          if (backgrounds[i]) select.value=backgrounds[i].id;
          const label = el('label',`PDF page ${i+1} → `); label.append(select); mapping.append(label); selects.push(select);
        }
        const update = () => {name.hidden=destination.value!=='new'; mapping.hidden=destination.value!=='replace';};
        destination.onchange=update; update();
        body.append(el('h3',`Import preview: ${job.pages} pages`), destination, name,
          el('p','Replacement keeps page IDs, handwriting and review history. Check every mapping below; mismatched page proportions are refused. Other pages remain unchanged.'), mapping);
        const progress = el('p','Ready to import.',{role:'status'}); body.append(progress);
        let targetBook=null, chosenMode=null;
        body.append(button('Import with these settings', async () => {
          cancelled=false;
          abort=new AbortController();
          const mode=chosenMode || destination.value;
          const targets=mode==='replace' ? selects.map(x=>x.value) : undefined;
          if (targets && (targets.some(x=>!x) || new Set(targets).size!==targets.length)) throw Error('Choose a distinct destination for every PDF page.');
          destination.disabled=true; name.disabled=true; selects.forEach(x=>x.disabled=true);
          try {
            for (let i=0;i<job.pages;i++) {
              if (cancelled) return;
              progress.textContent=`Preparing page ${i+1} of ${job.pages}…`;
              await request('/api/import',{action:'render',job:job.id,page:i},abort.signal);
            }
            if (cancelled) return;
            await api.save();
            if (!targetBook) {
              targetBook=mode==='new' ? (await request('/api/notebooks',{action:'create',name:name.value,kind:'notebook'})).notebook.id : s.book;
              chosenMode=mode;
            }
            const response=await fetch('/api/board?lazy=1&notebook='+encodeURIComponent(targetBook),{signal:abort.signal});
            if (!response.ok) throw Error('Could not read the import destination.');
            const base=mode==='new' ? response.headers.get('X-Board-Rev') : api.state().baseRev;
            await request('/api/import',{action:'commit',job:job.id,notebook:targetBook,base,targets},abort.signal);
            activeImport=null;
            request('/api/import',{action:'cancel',job:job.id}).catch(()=>{});
            progress.textContent='Import saved. Opening notebook…';
            if (targetBook===s.book) {await api.reconcile(); dialog.close();}
            else await api.navigate(targetBook,false);
          } catch (e) {
            progress.textContent='Import not committed. Staged pages are retained; you can retry or cancel.';
            throw e;
          } finally {destination.disabled=!!targetBook; selects.forEach(x=>x.disabled=false); name.disabled=!!targetBook;}
        }));
      } catch (e) {
        message(e.message);
      }
    };
    picker.onchange = () => chooseFile(picker.files[0]);
    if (sharedFile) await chooseFile(sharedFile);
    body.append(
      button('Cancel import/export', () => {
        cancelled = true;
        abort?.abort();
        if (activeImport)
          request('/api/import', { action: 'cancel', job: activeImport }).catch(() => {});
        activeImport = null;
        if (activeExport) request('/api/export-pdf',{action:'cancel',job:activeExport}).catch(()=>{});
        activeExport=null;
        message('Cancelled. Pages not committed remain temporary and expire automatically.');
      }),
    );
  }
  window.gptBoardImportFile = ({name, data} = {}) => {
    if (!api.state().store || dialog.open || typeof name !== 'string' || typeof data !== 'string' || data.length > 23000000) return false;
    try {
      const bytes = Uint8Array.from(atob(data), c=>c.charCodeAt(0));
      files(new File([bytes], name)).catch(e=>api.announce(e.message));
      return true;
    } catch { api.announce('Could not open the shared file. Try importing it from Files.'); return false; }
  };
  for (const [id, run] of Object.entries({
    library,
    'manage-pages': pages,
    'ai-panel': ai,
    recovery,
    files,
  }))
    document.getElementById(id).onclick = () =>
      Promise.resolve()
        .then(run)
        .catch((e) => {
          open('Action unavailable');
          message(e.message);
        });
}
