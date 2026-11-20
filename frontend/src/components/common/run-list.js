import {store} from '../../stores/research.js';
import {table} from './table.js';
import {status} from './status.js';
import {esc} from '../../utils/format.js';
const phase=p=>p?.phase==='completed'?'':p?.phase==='failed'?'':(p?.label?`${p.label} ${p.done}/${p.total}`:'');
export function runList(){return table(['编号 / 运行','状态','源数据快照','计算进度','复核'],store.runs.map(r=>[{html:`<a href="#results/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:status(r.status)},r.snapshot_count+'条 · '+r.checksum.slice(0,12),['queued','running'].includes(r.status)?esc(phase(r.progress)||'等待 worker'):(r.ready?'完整':'未完成'),r.review?'已复核':'待负责人复核']));}
