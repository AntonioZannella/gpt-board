// Records for the tutoring controller: saved check images and the per-frame ink log.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { once } from 'node:events'

const PORT = 8790 + Math.floor(Math.random() * 1000) % 3 + 7  // 8797-8799; avoids the main board's 8787
test('read_board saves the frame image; saves log only frames whose ink changed', async () => {
	const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'gpt-board-records-'))
	const root = new URL('../', import.meta.url)
	const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
	await fs.copyFile(new URL('server.py', root), path.join(tmp, 'server.py'))
	for(const support of ["board_storage.py", "board_review.py", "mcp_schema.json"]) await fs.copyFile(new URL(support, root),path.join(tmp,support));
 await fs.writeFile(path.join(tmp, 'package.json'), '{"type":"module"}')
	await fs.mkdir(path.join(tmp, 'dist'))
	await fs.writeFile(path.join(tmp, 'dist/index.html'), '<p>Test board</p>')
	const child = spawn(process.env.BOARD_PYTHON || 'python3', [path.join(tmp, 'server.py')], { env: { ...process.env, PORT: String(PORT) }, stdio: ['ignore', 'pipe', 'pipe'] })
	const controller = new AbortController()
	try {
		let err = ''
		child.stderr.on('data', (c) => { err += c })
		await Promise.race([once(child.stdout, 'data'), once(child, 'exit').then(() => { throw Error('Server exited: ' + err) }), sleep(3000).then(() => { throw Error('Server startup timeout') })])
		const base = `http://127.0.0.1:${PORT}`
		const post = (url, body) => fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
		const frame = (id, name) => ({ id, type: 'frame', typeName: 'shape', parentId: 'page:page', props: { name } })
		const draw = (id, parentId, pts) => ({ id, type: 'draw', typeName: 'shape', parentId, props: { segments: [{ points: pts }] } })
		const save = async (store) => {
			const r = await post(base + '/api/board', { tab: 'one', snapshot: { store, schema: { schemaVersion: 2 } }, summary: { shapeCount: Object.keys(store).length } })
			assert.equal(r.status, 200)
		}
		const activity = async () => (await fs.readFile(path.join(tmp, 'data/activity.jsonl'), 'utf8').catch(() => '')).trim().split('\n').filter(Boolean).map(JSON.parse)
		const F = { 'shape:f1': frame('shape:f1', '13.8-B P1'), 'shape:f2': frame('shape:f2', '13.8-B P2') }

		await save({ ...F })
		assert.equal((await activity()).length, 0, 'empty frames: nothing to log')
		const g = { 'shape:g': { id: 'shape:g', type: 'geo', typeName: 'shape', parentId: 'shape:f1', props: {} } }
		await save({ ...F, ...g, 'shape:d1': draw('shape:d1', 'shape:g', [1, 2]) })
		let a = await activity()
		assert.deepEqual(a.map((x) => x.frame), ['13.8-B P1'], 'ink inside a nested shape still belongs to its frame')
		assert.ok(a[0].t > 1e9 && a[0].ts.endsWith('Z'))
		await save({ ...F, ...g, 'shape:d1': draw('shape:d1', 'shape:g', [1, 2]) })
		assert.equal((await activity()).length, 1, 'an unchanged frame is not logged again')
		await save({ ...F, ...g, 'shape:d1': draw('shape:d1', 'shape:g', [1, 2, 3]), 'shape:d2': draw('shape:d2', 'shape:f2', [5]) })
		assert.deepEqual((await activity()).map((x) => x.frame).sort(), ['13.8-B P1', '13.8-B P1', '13.8-B P2'])
		await save({ ...F })
		assert.equal((await activity()).length, 3, 'a frame emptied (e.g. a new session loaded) is not logged as writing')

		// A board tab that answers read_board with a frame image.
		const res = await fetch(base + '/api/events?tab=one', { signal: controller.signal })
		;(async () => {
			let buf = ''
			for await (const chunk of res.body) {
				buf += new TextDecoder().decode(chunk)
				let end
				while ((end = buf.indexOf('\n\n')) >= 0) {
					const ev = buf.slice(0, end); buf = buf.slice(end + 2)
					if (!ev.startsWith('data: ')) continue
					const msg = JSON.parse(ev.slice(6))
					await post(base + '/api/ack', { opId: msg.opId, ok: true, summary: { frame: msg.frame }, png: 'data:image/png;base64,aGVsbG8=' })
				}
			}
		})().catch(() => {})
		const token = (await fs.readFile(path.join(tmp, 'data/token'), 'utf8')).trim()
		const rpc = async (args) => (await (await post(base + '/mcp/' + token, { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'read_board', arguments: args } })).json()).result
		const out = await rpc({ frame: '13.8-B P1' })
		assert.equal(out.content[0].type, 'image')
		const index = (await fs.readFile(path.join(tmp, 'data/checks/index.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse)
		assert.equal(index.length, 1)
		assert.equal(index[0].frame, '13.8-B P1')
		assert.equal((await fs.readFile(index[0].file)).toString(), 'hello', 'the saved image is the one the tutor got')
		assert.ok(path.basename(index[0].file).startsWith('13.8-B_P1-'))
		await rpc({ overview: true })
		assert.equal((await fs.readFile(path.join(tmp, 'data/checks/index.jsonl'), 'utf8')).trim().split('\n').length, 1, 'overviews are not saved as check images')
		console.log('PASS check images saved per frame read; ink log per changed frame, nested shapes, no repeats, no emptied frames')
	} finally {
		controller.abort()
		if (child.exitCode === null) { child.kill(); await Promise.race([once(child, 'exit'), sleep(2000)]) }
		await fs.rm(tmp, { recursive: true, force: true })
	}
})
