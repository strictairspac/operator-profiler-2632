import {datasets,plans,traces,renumbers} from '../api/business.js';
export const store={datasets:[],plans:[],traces:[],renumbers:[],renumberPreview:null,renumberOpen:null,user:null};
export async function refresh(){[store.datasets,store.plans,store.traces,store.renumbers]=await Promise.all([datasets.list(),plans.list(),traces.list(),renumbers.list()]);}
