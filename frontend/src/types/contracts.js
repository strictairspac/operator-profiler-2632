/** @typedef {{id:number,name:string,sensors:object[],readings:object[]}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,steps:object[]}} Plan */
/** @typedef {{id:number,name:string,status:'queued'|'running'|'completed'|'failed',result:object|null}} Trace */
/** @typedef {{id:number,dataset_id:number,status:'pending'|'conflicted'|'completed',mapping:Record<string,number>,preview:object,result:object|null,stale?:boolean}} RenumberRequest */
export {};
