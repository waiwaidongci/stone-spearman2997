import {studies,runs} from '../api/research.js';
export const store={studies:[],runs:[],studyId:0,runId:0,rows:[],rowsTotal:0,rowsOffset:0};
export async function load(){[store.studies,store.runs]=await Promise.all([studies.list(),runs.list()]);if(!store.studyId)store.studyId=store.studies[0]?.id||0;}
export const PAGE_SIZE=100;
export async function loadRows(offset=0){store.rowsOffset=Math.max(0,offset);const page=store.studyId?await studies.rows(store.studyId,store.rowsOffset,PAGE_SIZE):{total:0,rows:[]};store.rows=page.rows;store.rowsTotal=page.total;}
