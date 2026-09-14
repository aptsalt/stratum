// Copies the ADK-free Python modules into public/py so the demo build can load them in Pyodide.
import { cpSync, mkdirSync } from 'node:fs';

const MODULES = ['__init__', 'spec', 'ir', 'script', 'nl', 'codegen', 'templates', 'chat', 'sim', 'web'];
mkdirSync('public/py', { recursive: true });
for (const m of MODULES) cpSync(`../backend/stratum/${m}.py`, `public/py/${m}.py`);
console.log(`copied ${MODULES.length} modules to public/py`);
