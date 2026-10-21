// 本机 1.620 / FX 14.67 导出函数适配；独立 x86 进程，无窗口与 PLC 通信。
using System;
using System.IO;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Web.Script.Serialization;

class NativeFxSfc1620 {
 const string D="ECCodeGeneratorFX2.dll";
 const int Capacity=1048576;
 [DllImport("kernel32",CharSet=CharSet.Unicode)] static extern bool SetDllDirectory(string path);
 [DllImport("kernel32")] static extern uint SetErrorMode(uint mode);
 [DllImport("kernel32")] static extern IntPtr GetCurrentProcess();
 [DllImport("kernel32")] static extern bool TerminateProcess(IntPtr process,uint code);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern IntPtr ObjectNew();
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern void ObjectDelete(IntPtr p);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint Open(IntPtr p,ushort kind,IntPtr devices);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint SAPOpen(IntPtr p,IntPtr a,IntPtr b,IntPtr c,IntPtr d,IntPtr e,IntPtr f,ushort kind);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint InitializeCm(IntPtr p,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint WriteStepSymbol(IntPtr p,ushort x,ushort y,ushort type,ushort undef,ushort action,ushort number,ushort mode,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint WriteTransitionSymbol(IntPtr p,ushort x,ushort y,ushort number,ushort mode,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint WriteVerticalLine(IntPtr p,ushort x,ushort start,ushort end,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint WriteSelectiveBranch(IntPtr p,ushort start,ushort end,ushort y,ushort mode,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint WriteOperationOutput(IntPtr p,ushort number,IntPtr data,ushort size,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint WriteTransitionCondition(IntPtr p,ushort number,IntPtr data,ushort size,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint GetSizeOperationOutput(IntPtr p,ushort number,out ushort size,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint GetSizeTransitionCondition(IntPtr p,ushort number,out ushort size,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint ReadOperationOutput(IntPtr p,ushort number,IntPtr data,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint ReadTransitionCondition(IntPtr p,ushort number,IntPtr data,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint CheckSFCError(IntPtr p,IntPtr errors,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint ConvertSFCToPcode(IntPtr p,IntPtr bytes,out uint steps,out uint size);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint ConvertPcodeToSFC(IntPtr p,IntPtr bytes,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint ReadSymbol(IntPtr p,short x,short y,out short type,out short undef,out short sub,out short number,out short branch,out short result);
 [DllImport(D,CallingConvention=CallingConvention.Cdecl)] static extern uint ChangeSFCToPcode(IntPtr p,int count,IntPtr charts,IntPtr ladder,ref int size,IntPtr bytes);
 static void Check(string operation,uint code,short result=0) {
  if(code!=0||result!=0)throw new InvalidOperationException(operation+":"+code.ToString("X8")+":"+result);
 }
 static byte[] Read(IntPtr p,int size) {
  if(size<0||size>65536)throw new InvalidOperationException("输出超界");
  byte[] value=new byte[size];Marshal.Copy(p,value,0,size);return value;
 }
 static byte[] Bytes(object value) {
  byte[] b=Convert.FromBase64String((string)value);
  if(b.Length>65530)throw new InvalidOperationException("输入超界");return b;
 }
 static void Put(IntPtr p,byte[] b) {
  if(b.Length>Capacity)throw new InvalidOperationException("缓冲超界");
  Marshal.Copy(new byte[Capacity],0,p,Capacity);Marshal.Copy(b,0,p,b.Length);
 }
 static byte[] Encode(IntPtr p,IntPtr buffer) {
  uint steps,size;Check("ConvertSFCToPcode",ConvertSFCToPcode(p,buffer,out steps,out size));
  return Read(buffer,checked((int)size));
 }
 static List<object> Cells(IntPtr p) {
  var cells=new List<object>();
  for(short y=0;y<306;y++)for(short x=0;x<10;x++){
   short t,u,s,n,b,r;Check("ReadSymbol",ReadSymbol(p,x,y,out t,out u,out s,out n,out b,out r));
   if(r!=0)cells.Add(new{x=x,y=y,type=r==1?t:0,substep=r==1?s:0,number=r==3?0:n,branch=r==3?b:0,result=r});
  }
  return cells;
 }
 static Dictionary<string,object> WorkspaceBlock(Dictionary<string,object> block,byte[] graph) {
  byte[] original=Bytes(block["graphTemplate"]);int oldLength=BitConverter.ToInt32(original,12);
  int cacheOffset=12+oldLength;
  if(oldLength<4||cacheOffset+4>original.Length||BitConverter.ToInt32(original,0)!=original.Length
    ||BitConverter.ToInt32(original,cacheOffset)!=original.Length-cacheOffset)throw new InvalidOperationException("原生图形模板边界无效");
  byte[] replacement=new byte[16+graph.Length+original.Length-cacheOffset];
  Array.Copy(BitConverter.GetBytes(replacement.Length),0,replacement,0,4);
  Array.Copy(BitConverter.GetBytes(4+graph.Length),0,replacement,12,4);
  Array.Copy(graph,0,replacement,16,graph.Length);Array.Copy(original,cacheOffset,replacement,16+graph.Length,original.Length-cacheOffset);
  var bodies=new List<Dictionary<string,object>>();var numbers=(IList)block["actionNumbers"];
  foreach(Dictionary<string,object> row in (IList)block["bodies"]){
   byte[] tokens=Bytes(row["body"]),body=new byte[20+tokens.Length];
   Array.Copy(BitConverter.GetBytes(body.Length),0,body,0,4);body[4]=1;body[8]=12;
   Array.Copy(BitConverter.GetBytes(-1),0,body,16,4);Array.Copy(tokens,0,body,20,tokens.Length);
   int number=Convert.ToInt32(row["number"]);
   if((string)row["kind"]=="action")number=Convert.ToInt32(numbers[number]);
   bodies.Add(new Dictionary<string,object>{{"kind",row["kind"]},{"number",number},{"body",body}});
  }
  return new Dictionary<string,object>{{"name",block["name"]},{"graph",replacement},{"bodies",bodies}};
 }
 [STAThread] static int Main(string[] args) {
  Console.InputEncoding=System.Text.Encoding.UTF8;Console.OutputEncoding=new System.Text.UTF8Encoding(false);
  SetErrorMode(3);IntPtr p=IntPtr.Zero;var mem=new List<IntPtr>();bool workspaceMode=false;
  try {
   string dll=Path.Combine(args[0],D);
   using(var sha=SHA256.Create()) {
    string hash=BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(dll))).Replace("-","").ToLowerInvariant();
    if(IntPtr.Size!=4||hash!="7915fec25ecf9ab0a2a9ff7072ec23deb6187d83dbe86b885747ba29e4be99b8")
     throw new InvalidOperationException("不是已验证的 FX 14.67 原生库");
   }
   if(!SetDllDirectory(args[0]))throw new InvalidOperationException("原生库目录失败");
   var json=new JavaScriptSerializer();
   var request=json.Deserialize<Dictionary<string,object>>(Console.In.ReadToEnd());
   workspaceMode=request.ContainsKey("projectDirectory");
   var blocks=(IList)request["blocks"];
   if(blocks.Count<1||blocks.Count>2)throw new InvalidOperationException("目前仅验证一至两个程序块");
   for(int i=0;i<12;i++){IntPtr b=Marshal.AllocCoTaskMem(Capacity);Put(b,new byte[0]);mem.Add(b);}
   p=ObjectNew();if(p==IntPtr.Zero)throw new InvalidOperationException("原生对象创建失败");
   Check("Open",Open(p,0x208,mem[8]));
   Marshal.WriteInt16(mem[0],0,10);Marshal.WriteInt16(mem[0],2,306);
   Marshal.WriteInt16(mem[4],0,512);Marshal.WriteInt16(mem[4],2,512);
   Marshal.WriteInt16(mem[6],0,4096);Marshal.WriteInt16(mem[6],2,512);
   Check("SAPOpen",SAPOpen(p,mem[0],mem[1],mem[2],mem[3],mem[4],mem[6],3));
   var outputs=new List<object>();var workspaceBlocks=new List<Dictionary<string,object>>();int blockIndex=0;
   foreach(object item in blocks) {
    var block=(Dictionary<string,object>)item;short r;
    Check("InitializeCm",InitializeCm(p,out r),r);
    if(block.ContainsKey("tokens")){
     Put(mem[7],Bytes(block["tokens"]));Check("DecodeSource",ConvertPcodeToSFC(p,mem[7],out r),r);
    }else{
     var symbols=(IList)block["symbols"];
     if(symbols.Count>1024)throw new InvalidOperationException("符号数量超界");
     foreach(object symbol in symbols) {
      var row=(Dictionary<string,object>)symbol;Func<string,ushort> u=k=>Convert.ToUInt16(row[k]);
      uint code;string kind=(string)row["kind"];
      if(kind=="step")code=WriteStepSymbol(p,u("x"),u("y"),u("type"),0,u("action"),u("number"),1,out r);
      else if(kind=="transition")code=WriteTransitionSymbol(p,u("x"),u("y"),u("number"),1,out r);
      else if(kind=="line")code=WriteVerticalLine(p,u("x"),u("start"),u("end"),out r);
      else if(kind=="branch")code=WriteSelectiveBranch(p,u("start"),u("end"),u("y"),1,out r);
      else throw new InvalidOperationException("未知图形符号");
      Check(kind,code,r);
     }
    }
    Check("GraphCheck",CheckSFCError(p,mem[5],out r),r);
    byte[] graph=Encode(p,mem[7]);
    var bodies=(IList)block["bodies"];
    foreach(object body in bodies){
     var row=(Dictionary<string,object>)body;byte[] b=Bytes(row["body"]);Put(mem[5],b);
     ushort number=Convert.ToUInt16(row["number"]);bool action=(string)row["kind"]=="action";
     uint code=action?WriteOperationOutput(p,number,mem[5],(ushort)b.Length,out r)
                     :WriteTransitionCondition(p,number,mem[5],(ushort)b.Length,out r);
     Check("WriteBody",code,r);
    }
    Check("BoundGraphCheck",CheckSFCError(p,mem[5],out r),r);
    byte[] compiled=Encode(p,mem[7]);
    // 编译后清空并重新解析，避免把原对象中的值当成读回证据。
    Check("ClearForReadback",InitializeCm(p,out r),r);Put(mem[7],compiled);
    Check("DecodeCompiled",ConvertPcodeToSFC(p,mem[7],out r),r);
    var readback=new List<object>();
    foreach(object body in bodies){
     var row=(Dictionary<string,object>)body;ushort size,number=Convert.ToUInt16(row["number"]);
     bool action=(string)row["kind"]=="action";
     uint code=action?GetSizeOperationOutput(p,number,out size,out r):GetSizeTransitionCondition(p,number,out size,out r);
     Check("BodySize",code,r);Put(mem[5],new byte[0]);
     if(size>0){
      code=action?ReadOperationOutput(p,number,mem[5],out r):ReadTransitionCondition(p,number,mem[5],out r);
      Check("ReadBody",code,r);
     }
     readback.Add(new{kind=row["kind"],number=number,body=Convert.ToBase64String(Read(mem[5],size))});
    }
    // 14.67 采用内嵌数组 ABI，尺寸来自本机导出函数的实际拷贝长度。
    Marshal.Copy(Read(mem[0],0x5408),0,IntPtr.Add(mem[9],blockIndex*0x5408),0x5408);
    Marshal.Copy(Read(mem[1],0xfffc),0,IntPtr.Add(mem[10],blockIndex*0xfffc),0xfffc);
    outputs.Add(new{graph=Convert.ToBase64String(graph),compiled=Convert.ToBase64String(compiled),
                    cells=Cells(p),bodies=readback});
    if(workspaceMode)workspaceBlocks.Add(WorkspaceBlock(block,graph));
    blockIndex++;
   }
   int length=65536;Check("CompileBlocks",ChangeSFCToPcode(p,blocks.Count,mem[9],mem[10],ref length,mem[7]));
   byte[] program=Read(mem[7],length);
   var response=new Dictionary<string,object>{{"ok",true},{"blocks",outputs},{"program",Convert.ToBase64String(program)},
     {"nativeVersion","14.67"},{"compileKind","native-sfc-common-program"}};
   if(workspaceMode){
    ObjectDelete(p);p=IntPtr.Zero;foreach(IntPtr buffer in mem)Marshal.FreeCoTaskMem(buffer);mem.Clear();
    response["project"]=WorkspaceSfc1620.Run(Path.GetDirectoryName(Path.GetDirectoryName(args[0])),(string)request["projectDirectory"],workspaceBlocks);
   }
   Console.WriteLine(json.Serialize(response));Console.Out.Flush();
   // 原生工作区的 DLL 卸载会等待后台线程；仅结束本次已完成提交的辅助进程。
   if(workspaceMode)TerminateProcess(GetCurrentProcess(),0);
   return 0;
  }catch(Exception e){Console.Error.WriteLine(e.ToString());Console.Error.Flush();if(workspaceMode)TerminateProcess(GetCurrentProcess(),1);return 1;}
  finally{if(p!=IntPtr.Zero)ObjectDelete(p);foreach(IntPtr b in mem)Marshal.FreeCoTaskMem(b);}
 }
}
