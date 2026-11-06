import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import net from 'node:net'
import { spawn, execFileSync } from 'node:child_process'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../', import.meta.url))
const controller = process.env.MLS_TEST_CONTROLLER || path.resolve(root, '../Math Learning System/mls.py')
const python = process.env.MLS_TEST_PYTHON || 'python3'
const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

test('real controller: MCP reviews preserve evidence, record distinct errors, and retry idempotently', async () => {
    const tmp = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'gpt-board-review-')))
    const courseDir = path.join(tmp, 'mls')
    const errorLog = path.join(tmp, 'MathErrorLog.md')
    let child
    const connection = new AbortController()
    try {
        await fs.copyFile(path.join(root, 'server.py'), path.join(tmp, 'server.py'))
        for(const support of ["board_storage.py", "board_review.py", "http_body.py", "mcp_schema.json"]) await fs.copyFile(new URL(support, new URL('../',import.meta.url)),path.join(tmp,support));
 await fs.writeFile(path.join(tmp, 'package.json'), '{"type":"module"}')
        await fs.writeFile(errorLog, '# Isolated test error log\n')
        execFileSync(python, [controller, '--dir', courseDir, 'init', '--course', 'Calc 3', '--error-log', errorLog], { stdio: 'pipe' })
        const reserve = net.createServer()
        reserve.listen(0, '127.0.0.1')
        await once(reserve, 'listening')
        const port = reserve.address().port
        await new Promise(resolve => reserve.close(resolve))
        child = spawn(process.env.BOARD_PYTHON || 'python3', [path.join(tmp, 'server.py')], {
            env: { ...process.env, PORT: String(port), MLS_CONTROLLER: controller, MLS_PYTHON: python, MLS_COURSES_JSON: JSON.stringify({ 'Calc 3': courseDir }) },
            stdio: ['ignore', 'pipe', 'pipe'],
        })
        let stderr = ''
        child.stderr.on('data', data => { stderr += data })
        await Promise.race([once(child.stdout, 'data'), once(child, 'exit').then(() => { throw Error(stderr || 'Server exited') }), sleep(3000).then(() => { throw Error('Startup timeout') })])
        const base = `http://127.0.0.1:${port}`
        const token = (await fs.readFile(path.join(tmp, 'data/token'), 'utf8')).trim()
        const post = (url, body) => fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
        let serial = 0
        const rpc = async (method, params) => (await (await post(base + '/mcp/' + token, { jsonrpc: '2.0', id: ++serial, method, params })).json()).result
        const call = (name, args) => rpc('tools/call', { name, arguments: args })
        const parsed = result => JSON.parse(result.content.find(item => item.type === 'text').text)

        const init = await rpc('initialize', {})
        assert.match(init.instructions, /record_review/)
        assert.match(init.instructions, /Never invent unclear handwriting/)
        const tools = (await rpc('tools/list', {})).tools
        const reviewTool = tools.find(tool => tool.name === 'record_review')
        assert.ok(reviewTool)
        assert.equal(reviewTool.annotations.readOnlyHint, false)
        assert.deepEqual(reviewTool.inputSchema.properties.course.enum, ['Calc 3'])

        // A simulated board tab only returns a synthetic one-pixel test image.
        const receivedOps = []
        let legacyRegionClient = false
        const stream = await fetch(base + '/api/events?tab=review-test', { signal: connection.signal })
        ;(async () => {
            let buffer = ''
            for await (const bytes of stream.body) {
                buffer += new TextDecoder().decode(bytes)
                let end
                while ((end = buffer.indexOf('\n\n')) >= 0) {
                    const event = buffer.slice(0, end)
                    buffer = buffer.slice(end + 2)
                    if (!event.startsWith('data: ')) continue
                    const op = JSON.parse(event.slice(6))
                    receivedOps.push(op)
                    await post(base + '/api/ack', { opId: op.opId, ok: true, summary: op.frame ? { frame: op.frame, ...(op.region && !legacyRegionClient && { region: op.region }) } : { frames: ['Test page 7'] }, ...(op.frame && { png }) })
                }
            }
        })().catch(() => {})

        const listing = parsed(await call('read_board', {}))
        assert.match(listing.review_instructions, /record_review/)
        assert.equal(listing.review_context, undefined, 'listing does not fabricate evidence')
        assert.equal((await call('read_board', { frame: 'Test page 7', region: { top: -1, height: 100 } })).isError, true)
        assert.equal((await call('read_board', { region: { top: 0, height: 100 } })).isError, true)
        const read = async (region) => {
            const out = await call('read_board', { frame: 'Test page 7', ...(region && { region }) })
            assert.equal(out.content[0].type, 'image')
            return parsed(out).review_context
        }
        const context = await read()
        assert.equal(context.notebook, 'default')
        assert.equal(context.frame, 'Test page 7')
        const baseReview = { check_id: context.check_id, course: 'Calc 3', section: '13.8', page: '7', problem: '1.1', original_response: 'Synthetic fixture: x=2; fxx=2; minimum.', result: 'right', read_confidence: 'high', assistance: 'unknown', errors: [] }
        const right = parsed(await call('record_review', baseReview))
        assert.equal(right.status, 'saved')
        assert.equal(right.result, 'right')
        assert.equal(right.error_count, 0)
        assert.equal(right.error_log, errorLog)
        const duplicate = parsed(await call('record_review', baseReview))
        assert.equal(duplicate.status, 'already_saved')
        assert.equal(duplicate.review_id, right.review_id)

        const rows = async () => (await fs.readFile(path.join(courseDir, 'attempts.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse)
        let records = await rows()
        assert.equal(records.length, 1)
        assert.equal(records[0].kind, 'board_review')
        assert.equal(records[0].evidence.check_id, context.check_id)
        assert.ok(records[0].evidence.image.startsWith(path.join(tmp, 'data/checks')))
        assert.ok((await fs.readFile(records[0].evidence.image)).length > 0)
        assert.equal((await call('record_review', { ...baseReview, original_response: 'Different assessment of the same check' })).isError, true, 'changed retry must conflict')
        assert.equal((await call('record_review', { ...baseReview, check_id: 'check-00000000-0000-0000-0000-000000000000' })).isError, true, 'missing evidence must fail')
        assert.equal((await call('record_review', { ...baseReview, course: 'Unconfigured course' })).isError, true)

        const oneError = { error_id: 'e1', type: 'TOOL', skill: 'second derivative test', first_wrong_step: 'line 3', original_work: 'D = fxx + fyy', cause: 'unknown', correction: 'D = fxx*fyy - fxy^2', reasoning: 'The classification test uses the Hessian determinant, not its trace.', follow_up: 'Recompute D and classify a fresh quadratic.', confidence: 'high' }
        assert.equal((await call('record_review', { ...baseReview, result: 'wrong', read_confidence: 'low', errors: [oneError] })).isError, true, 'unclear handwriting is never attributed as an error')
        const unresolved = parsed(await call('record_review', { ...baseReview, problem: '1.2', original_response: 'Final symbol unreadable; needs clarification.', result: 'unresolved', read_confidence: 'low' }))
        assert.equal(unresolved.result, 'unresolved')
        assert.equal(unresolved.error_count, 0)

        legacyRegionClient = true
        const indexPath = path.join(tmp, 'data/checks/index.jsonl')
        const beforeLegacyRead = await fs.readFile(indexPath, 'utf8')
        const legacyRead = await call('read_board', { frame: 'Test page 7', region: { top: 200, height: 600 } })
        assert.equal(legacyRead.isError, true)
        assert.match(legacyRead.content[0].text, /Refresh the GPT Board tab/)
        assert.equal(await fs.readFile(indexPath, 'utf8'), beforeLegacyRead, 'old clients must not archive a full page mislabeled as a slice')
        legacyRegionClient = false
        const next = await read({ top: 200, height: 600 })
        assert.deepEqual(next.region, { top: 200, height: 600 })
        assert.deepEqual(receivedOps.at(-1).region, { top: 200, height: 600 })
        assert.notEqual(next.check_id, context.check_id, 'each archived read is distinct')
        const secondError = { ...oneError, error_id: 'e2', type: 'ALGEBRA', skill: 'substitution', first_wrong_step: 'line 4', original_work: '2*3 = 5', correction: '2*3 = 6', reasoning: 'Multiplication gives six.', follow_up: 'Recompute the product and update the final classification.' }
        const wrong = parsed(await call('record_review', { ...baseReview, check_id: next.check_id, result: 'wrong', original_response: 'Synthetic fixture: D=fxx+fyy; 2*3=5.', errors: [oneError, secondError], evidence: { image: '/forged/path' } }))
        assert.equal(wrong.status, 'saved')
        assert.equal(wrong.error_count, 2)
        records = await rows()
        assert.equal(records.length, 3)
        assert.equal(records[2].errors.length, 2)
        assert.ok(records[2].evidence.image.startsWith(path.join(tmp, 'data/checks')), 'caller cannot forge archived image provenance')
        const log = await fs.readFile(errorLog, 'utf8')
        assert.match(log, /Hessian determinant/)
        assert.match(log, /Multiplication gives six/)
        assert.match(log, /Final symbol unreadable/)
        const events = await fs.readFile(path.join(courseDir, 'events.jsonl'), 'utf8').catch(() => '')
        assert.ok(!events.split('\n').filter(Boolean).map(JSON.parse).some(event => event.cmd === 'start'), 'reviews never silently create a tutoring session')
    } finally {
        connection.abort()
        if (child && child.exitCode === null) { child.kill(); await Promise.race([once(child, 'exit'), sleep(2000)]) }
        await fs.rm(tmp, { recursive: true, force: true })
    }
})
