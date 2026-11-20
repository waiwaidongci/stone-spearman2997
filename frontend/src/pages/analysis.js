import {DOMAIN} from '../constants/domain.js';
import {store,load} from '../stores/research.js';
import {runs} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {runList} from '../components/common/run-list.js';
import {bindForm} from '../hooks/forms.js';
import {esc} from '../utils/format.js';
const labels={split_penalty:'每次切分罚分'};
const SEG=DOMAIN.segment;
export function render(){const writable=localStorage.getItem('research-role')!=='viewer';const study=store.studies.find(s=>s.id===store.studyId);const missing=study?.missing_sequence||0;const short=study&&study.row_count-SEG.min_rows<0;return `<div class="eyebrow">02 / METHOD WORKSHEET</div><h1>${DOMAIN.field_label}</h1><p class="lead">${DOMAIN.algorithm}</p><div class="method-note">${DOMAIN.note}</div>${missing?`<div class="error">当前批次有 ${missing} 条试件缺少生产序号；发起研究前必须全部补齐。</div>`:''}${short?`<div class="error">当前批次不足 ${SEG.min_rows} 件，无法分段拟合。</div>`:''}${writable?`<form id="run-form" class="worksheet">${studySelect()}<label>本次研究名称<input name="name" required minlength="2" value="${esc(DOMAIN.names[0])} · 分段核算"></label><div class="inline-form">${Object.entries(DOMAIN.defaults).map(([key,value])=>`<label>${labels[key]}<input name="${key}" type="number" step="any" min="${SEG.penalty_min}" max="${SEG.penalty_max}" value="${value}" required></label>`).join('')}</div><p>每段连续 ${SEG.min_rows} 至 ${SEG.max_rows} 件；孔隙率恒定段只拟合吸水率均值。提交时冻结生产序号、全部测值与罚分并生成 SHA256，后台 worker 独立执行，中断可续算。</p><button type="submit">冻结输入并排队计算 →</button></form>`:'<p class="method-note">观察员可阅读研究记录，分析师与负责人可以发起计算。</p>'}<h2>计算记录</h2>${runList()}`;}
export function bind(refresh){bindForm('run-form',async data=>{const parameters=Object.fromEntries(Object.keys(DOMAIN.defaults).map(k=>[k,+data[k]]));const r=await runs.create({study_id:+data.study_id,name:data.name,parameters});store.runId=r.id;await load();location.hash='results/'+r.id;});const sel=document.getElementById('study-select');if(sel)sel.onchange=()=>{store.studyId=+sel.value;refresh();};}
