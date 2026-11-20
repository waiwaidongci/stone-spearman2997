import {store} from '../stores/research.js';
import {runs} from '../api/research.js';
import {esc,fmt} from '../utils/format.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {runList} from '../components/common/run-list.js';
import {download} from '../utils/download.js';
const phaseText=p=>p?.phase==='completed'?'已完成':p?.phase==='failed'?'失败':`${p?.label||''} ${p?.done??0} / ${p?.total??0}`;
const segmentTable=r=>table(r.result.columns,r.result.rows);
const cutTable=r=>r.result.mandatory_cut_rows.length?table(r.result.mandatory_columns,r.result.mandatory_cut_rows):'<p class="method-note">没有所有最优方案共同要求的切分位置。</p>';
export async function render(id){if(!id)return `<h1>计算结果与解释</h1><p>选择一次运行查看冻结输入、算法结果与业务解释。</p>${runList()}`;const r=await runs.get(id,true);store.runId=r.id;const ready=r.status==='completed'&&r.ready;return `<div class="eyebrow">03 / RESULTS ARCHIVE · RUN ${r.id}</div><h1>${esc(r.name)} ${status(r.status)}</h1><div class="checksum">INPUT SHA256 ${r.checksum}</div><p>快照 ${r.snapshot_count} 条 · 切分罚分 ${esc(r.parameters.split_penalty)} · ${esc(r.created_at)} · ${r.review?'负责人已复核':'尚未复核'}</p>${r.status==='failed'?`<div class="error">计算失败：${esc(r.error)}。请到来源页修复数据，然后创建新运行。</div>`:ready?`<section class="result-intro"><strong>${esc(r.result.interpretation)}</strong><p>${esc(r.result.caveat)}</p></section><h2>核心数值</h2>${table(['业务指标','计算值'],Object.entries(r.result.metrics).map(([k,v])=>[k,fmt(v)]))}<h2>必切位置 <small>所有最优方案共同要求</small></h2>${cutTable(r)}<h2>${esc(r.result.table_title||'最优分段')}</h2>${segmentTable(r)}<div class="toolbar"><button id="export">下载核算 CSV</button><a class="button secondary" href="#review/${r.id}">打开业务复核 →</a></div>`:`<p class="method-note">任务正在计算：${esc(phaseText(r.progress))}。此页每2秒更新；中断后 worker 从已确认前缀继续，不重算。</p>`}<details><summary>本次冻结的参数${r.snapshot_omitted?'（源记录过多，仅存于数据库快照）':'与源记录'}</summary><pre>${esc(JSON.stringify({parameters:r.parameters,...(r.snapshot?{snapshot:r.snapshot}:{})},null,2))}</pre></details>`;}
export function bind(){const button=document.getElementById('export');if(button)button.onclick=async()=>{try{download(await runs.export(store.runId),'research-'+store.runId+'.csv');}catch(e){document.getElementById('notice').textContent=e.message;}};}
