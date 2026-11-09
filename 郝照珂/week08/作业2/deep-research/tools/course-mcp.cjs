// Small JSON-RPC MCP stdio server. stdout is reserved exclusively for protocol messages.
const readline = require('node:readline');
const fs = require('node:fs');
const path = require('node:path');
const tools = [{name:'course_checklist', description:'Return the Week 08 homework requirements and project research pipeline. Read-only.',
  inputSchema:{type:'object',properties:{week:{type:'integer',enum:[8]}},required:['week'],additionalProperties:false}}];
function reply(id, result) { process.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\n'); }
readline.createInterface({input:process.stdin}).on('line', line => {
  let m;
  try {
    m = JSON.parse(line);
    if (!('id' in m)) return;
    if (m.method === 'initialize') return reply(m.id,{protocolVersion:m.params.protocolVersion,
      capabilities:{tools:{}}, serverInfo:{name:'week08-course-tools',version:'1.0.0'}});
    if (m.method === 'ping') return reply(m.id,{});
    if (m.method === 'tools/list') return reply(m.id,{tools});
    if (m.method === 'tools/call') {
      if (m.params.name !== 'course_checklist' || m.params.arguments?.week !== 8)
        return reply(m.id,{isError:true,content:[{type:'text',text:'Expected course_checklist with week=8'}]});
      const result={week:8,assignment1:['Claude Code Skill','Hook','MCP','Separate screenshots'],
        assignment2:'Deep Research Assistant',pipeline:['plan','search','extract','judge','report'],
        required_outputs:['structured_report','sources','process','confidence'],marker:'WEEK08_MCP_OK'};
      const dir=path.resolve(__dirname,'../evidence'); fs.mkdirSync(dir,{recursive:true});
      fs.appendFileSync(path.join(dir,'mcp-calls.jsonl'),JSON.stringify({time:new Date().toISOString(),tool:m.params.name,arguments:m.params.arguments,result})+'\n');
      return reply(m.id,{content:[{type:'text',text:JSON.stringify(result)}]});
    }
    process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:m.id,error:{code:-32601,message:'Method not found'}})+'\n');
  } catch {
    process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:m?.id??null,error:{code:-32700,message:'Invalid request'}})+'\n');
  }
});
