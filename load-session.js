// Compatibility entry point. All implementation lives in load_session.py.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const child=spawn(process.env.BOARD_PYTHON||'python3',[fileURLToPath(new URL('./load_session.py',import.meta.url)),...process.argv.slice(2)],{stdio:'inherit'});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
child.on('error',error=>{console.error(error.message);process.exit(1)});
child.on('exit',code=>process.exit(code??1));
