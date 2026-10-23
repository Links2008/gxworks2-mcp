"""仿真工具的真实 MCP 握手；默认只读取，不启动或修改虚拟 CPU。"""
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root=Path(__file__).resolve().parents[1]
    params=StdioServerParameters(command=sys.executable,args=['-m','gxworks2_mcp'],cwd=str(root),
                                env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize();listed=await client.list_tools()
            names={t.name for t in listed.tools}
            required={'gx_simulator_'+n for n in ('start','stop','status','control','read','inputs','press','verify')}
            assert required <= names
            async def call(name,arguments):
                response=await client.call_tool(name,arguments)
                result=json.loads(response.content[0].text)
                print(name,json.dumps(result,ensure_ascii=False),flush=True)
                return result
            assert not (await call('gx_simulator_inputs',{'values':{'X0':1}}))['ok']
            status=await call('gx_simulator_status',{})
            if status.get('active'):
                assert (await call('gx_simulator_read',{'devices':['X0','Y0','M8000','S0','TN250']}))['ok']
            if os.environ.get('GXWORKS2_SIMULATOR_LIVE_TEST')=='1':
                assert (await call('gx_simulator_stop',{'confirm':True}))['ok']
                assert (await call('gx_simulator_start',{'confirm':True}))['transferConfirmed']
                assert (await call('gx_simulator_control',{'action':'run','confirm':True}))['runState']=='run'
                initial={'X0':0,'X1':0,'X2':0,'X3':0,'X4':0,'X5':1,'X6':0,'X7':0,'X10':0,'X11':1,'X12':0}
                assert (await call('gx_simulator_inputs',{'values':initial,'confirm':True}))['ok']
                assert (await call('gx_simulator_press',{'device':'X0','duration_ms':100,'confirm':True}))['released']
                assert (await call('gx_simulator_verify',{'steps':[{'expect':{'X0':0,'Y0':1,'S20':1}},{'inputs':{'X1':1},'expect':{'M0':0,'Y0':0}}],'confirm':True}))['ok']
                assert (await call('gx_simulator_control',{'action':'stop','confirm':True}))['runState']=='stop'
    print('8 simulator tools discovered; MCP transport verified')


if __name__=='__main__':asyncio.run(main())
