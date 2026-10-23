// 接口初始化顺序参考 Arienax/gxworks-agent (Apache-2.0)，按本机 1.620 类型库及实测适配。
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Threading;
using System.Collections;
using System.Collections.Generic;

static class WorkspaceSfc1620 {
 [DllImport("ole32")] static extern int CoInitializeEx(IntPtr p,uint mode);
 [DllImport("ole32")] static extern int CoCreateInstance(ref Guid clsid,IntPtr outer,uint context,ref Guid iid,out IntPtr instance);
 [DllImport("kernel32",CharSet=CharSet.Unicode)] static extern bool SetDllDirectory(string path);
 [StructLayout(LayoutKind.Sequential)] struct Id{[MarshalAs(UnmanagedType.ByValArray,SizeConst=12)]public uint[] Words;}
 [StructLayout(LayoutKind.Sequential)] struct App {public int Kind;public IntPtr Language;public uint Page,ProjectPage,Series,Type,References;}
 [StructLayout(LayoutKind.Sequential)] struct Project {public IntPtr Cpu,Name,Extra;public int Kind,Mode;}
 [StructLayout(LayoutKind.Sequential)] struct Attributes {public int Id;public IntPtr Name;public int Created,Crypto,Attribute,UserManage;public IntPtr Cpu,Title;public int Mode;}
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Self(IntPtr s);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Code(IntPtr s,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IntCode(IntPtr s,int value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IntCall(IntPtr s,int value);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int TwoInt(IntPtr s,int first,int second);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Pointer(IntPtr s,IntPtr value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int AppCall(IntPtr s,IntPtr value,ref IntPtr extra,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Support(IntPtr s,int count,IntPtr value,int group);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Cpu(IntPtr s,ref IntPtr value,int group);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Reserve(IntPtr s,IntPtr project,IntPtr parameter,IntPtr reserved,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Function(IntPtr s,int kind,out IntPtr pointer,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FileName(IntPtr s,IntPtr file,out IntPtr name,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Import(IntPtr s,IntPtr home,IntPtr workspace,IntPtr project,IntPtr file,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int GetAttr(IntPtr s,IntPtr home,IntPtr workspace,IntPtr project,out Attributes attributes,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int OpenCall(IntPtr s,int type,IntPtr home,IntPtr workspace,IntPtr project,int label,uint mode,int operation,out Id id,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProjectFunction(IntPtr s,Id id,int kind,out IntPtr value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Collection(IntPtr s,Id id,int kind,out Id value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Count(IntPtr s,Id id,out int count,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdCode(IntPtr s,Id id,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int List(IntPtr s,Id id,ref int count,IntPtr buffer,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProgramCheck(IntPtr s,Id id,int mask,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Find(IntPtr s,Id id,int kind,IntPtr name,out Id value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Zoom(IntPtr s,Id id,int kind,int number,out Id value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int WriteBody(IntPtr s,Id id,int size,IntPtr data,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ReadBody(IntPtr s,Id id,ref int size,IntPtr data,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdInt(IntPtr s,Id id,int value,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Progress(IntPtr s,ref int percent,ref int count,ref IntPtr reports,ref int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Save(IntPtr s,IntPtr home,IntPtr workspace,IntPtr project,uint mode,Id id,int succession,out int code);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Export(IntPtr s,IntPtr home,IntPtr workspace,IntPtr project,IntPtr file,int history,int overwrite,uint split,out int code);

 static T Slot<T>(IntPtr p,int offset){return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(p),offset),typeof(T));}
 static void Check(string name,int hr,int code=0){if(hr<0||code!=0)throw new InvalidOperationException(name+":"+hr.ToString("X8")+":"+code.ToString("X8"));}
 static IntPtr B(string s){return Marshal.StringToBSTR(s);}
 static string Text(IntPtr pointer){
  if(pointer==IntPtr.Zero)return "";int n=Marshal.ReadInt32(pointer,-4);
  if(n<0||n>32768||(n&1)!=0)throw new InvalidOperationException("原生文本超界");
  return Marshal.PtrToStringUni(pointer,n/2);
 }
 static IntPtr Create(string clsid,string iid){Guid cls=new Guid(clsid),contract=new Guid(iid);IntPtr p;Check("Create",CoCreateInstance(ref cls,IntPtr.Zero,1,ref contract,out p));return p;}
 static IntPtr Query(IntPtr p,string iid){Guid contract=new Guid(iid);IntPtr value;Check("Query",Marshal.QueryInterface(p,ref contract,out value));return value;}
 static IntPtr Alloc(int size){IntPtr p=Marshal.AllocCoTaskMem(size);Marshal.Copy(new byte[size],0,p,size);return p;}
 static void VerifyModule(string root,string name){
  foreach(ProcessModule module in Process.GetCurrentProcess().Modules)if(module.ModuleName.Equals(name,StringComparison.OrdinalIgnoreCase)){
   string expected=Path.Combine(root,"DNaviZero","DataAbsorber",name);
   if(!Path.GetFullPath(module.FileName).Equals(expected,StringComparison.OrdinalIgnoreCase)||module.FileVersionInfo.FileVersion!="1.620.0.1")
    throw new InvalidOperationException("原生模块路径或版本不匹配："+name);
   return;
  }
  throw new InvalidOperationException("原生模块未加载："+name);
 }
 static List<Id> Children(IntPtr ws,Id owner){
  int n,c;Check("ChildCount",Slot<Count>(ws,24)(ws,owner,out n,out c),c);
  if(n<0||n>1024)throw new InvalidOperationException("子对象数量超界");
  var result=new List<Id>();if(n==0)return result;IntPtr buffer=Alloc(n*48);int capacity=n;
  try{
   Check("ChildList",Slot<List>(ws,44)(ws,owner,ref n,buffer,out c),c);
   if(n<0||n>capacity)throw new InvalidOperationException("子对象数量改变");
   for(int i=0;i<n;i++)result.Add((Id)Marshal.PtrToStructure(IntPtr.Add(buffer,i*48),typeof(Id)));
  }finally{Marshal.FreeCoTaskMem(buffer);}return result;
 }
 static void PutBody(IntPtr ws,Id body,byte[] data){
  if(data.Length<4||data.Length>1048576||BitConverter.ToInt32(data,0)!=data.Length)throw new InvalidOperationException("原生程序体边界无效");
  IntPtr buffer=Alloc(data.Length);int c;
  try{
   Marshal.Copy(data,0,buffer,data.Length);Check("WriteBody",Slot<WriteBody>(ws,1688)(ws,body,data.Length,buffer,out c),c);
   int length=data.Length;Check("ReadBody",Slot<ReadBody>(ws,1692)(ws,body,ref length,buffer,out c),c);
   if(length!=data.Length)throw new InvalidOperationException("原生程序体读回长度不一致");
   byte[] actual=new byte[length];Marshal.Copy(buffer,actual,0,length);
   for(int i=0;i<length;i++)if(actual[i]!=data[i])throw new InvalidOperationException("原生程序体读回不一致");
  }finally{Marshal.FreeCoTaskMem(buffer);}
  Check("Edited",Slot<IdInt>(ws,68)(ws,body,1,out c),c);
  Check("CompileChanged",Slot<IdInt>(ws,1560)(ws,body,1,out c),c);
 }
 static void Edit(IntPtr ws,Id project,IList blocks){
  foreach(Dictionary<string,object> row in blocks){
   Id owner,body;int c,kind;
   Check("FindOwner",Slot<Find>(ws,1784)(ws,project,26,B((string)row["name"]),out owner,out c),c);
   Check("OwnerType",Slot<Count>(ws,48)(ws,owner,out kind,out c),c);if(kind!=60)throw new InvalidOperationException("目标不是 SFC 程序块");
   Check("FindBody",Slot<Find>(ws,1784)(ws,project,32,B((string)row["name"]),out body,out c),c);
   // 只重建本次隔离工程中指定程序块的动作与条件。
   for(int i=0;i<1024;i++){
    bool removed=false;
    foreach(Id child in Children(ws,owner)){
     Check("ChildType",Slot<Count>(ws,48)(ws,child,out kind,out c),c);
     if(kind==62||kind==66||kind==68){Check("RemoveChild",Slot<IdCode>(ws,20)(ws,child,out c),c);removed=true;break;}
    }
    if(!removed)break;if(i==1023)throw new InvalidOperationException("子对象清理超界");
   }
   PutBody(ws,body,(byte[])row["graph"]);
   foreach(Dictionary<string,object> child in (IList)row["bodies"]){
    kind=(string)child["kind"]=="action"?0:1;int number=Convert.ToInt32(child["number"]);Id created,zoom;
    Check("CreateZoom",Slot<Zoom>(ws,3000)(ws,owner,kind,number,out created,out c),c);
    Check("GetZoom",Slot<Zoom>(ws,3004)(ws,owner,kind==0?2:1,number,out zoom,out c),c);
    PutBody(ws,zoom,(byte[])child["body"]);
   }
  }
 }
 static List<Dictionary<string,object>> Poll(IntPtr compiler,int slot,bool checking){
  var all=new List<Dictionary<string,object>>();bool failed=false;
  for(int poll=0;poll<200;poll++){
   System.Windows.Forms.Application.DoEvents();int percent=0,count=0,c=0;IntPtr reports=IntPtr.Zero;
   Check("Progress",Slot<Progress>(compiler,slot)(compiler,ref percent,ref count,ref reports,ref c),c);
   if(count<0||count>1000||(count>0&&reports==IntPtr.Zero)||percent<0||percent>100||all.Count+count>1000)
    throw new InvalidOperationException("原生诊断边界无效");
   for(int i=0;i<count;i++){
    IntPtr row=IntPtr.Add(reports,i*100);int kind=Marshal.ReadInt32(row),code=Marshal.ReadInt32(row,4);
    var arguments=new List<string>();IntPtr list=Marshal.ReadIntPtr(row,96);
    if(list!=IntPtr.Zero){
     IntPtr values=Marshal.ReadIntPtr(list);int n=Marshal.ReadInt32(list,4);
     if(n<0||n>128||(n>0&&values==IntPtr.Zero))throw new InvalidOperationException("原生诊断参数超界");
     for(int j=0;j<n;j++)arguments.Add(Text(Marshal.ReadIntPtr(values,j*4)));
    }
    all.Add(new Dictionary<string,object>{{"kind",kind},{"code",code},{"name",Text(Marshal.ReadIntPtr(row,8))},
     {"step",Marshal.ReadInt32(row,72)},{"arguments",arguments}});
    failed|=kind==2||(kind==1&&code==32);
   }
   if(percent==100){
    if(failed)throw new InvalidOperationException("原生检查拒绝："+new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(all));
    var endings=all.FindAll(r=>(int)r["kind"]==1&&(int)r["code"]==(checking?0x23:0x104));
    if(endings.Count!=1)throw new InvalidOperationException("原生诊断缺少完成记录");
    if(checking){
     var args=(List<string>)endings[0]["arguments"];int errors,warnings;
     if(args.Count!=2||!Int32.TryParse(args[0],out errors)||!Int32.TryParse(args[1],out warnings)||errors!=0
       ||warnings!=all.FindAll(r=>(int)r["kind"]==3).Count)throw new InvalidOperationException("原生诊断计数不完整");
    }
    return all;
   }
   Thread.Sleep(10);
  }
  throw new TimeoutException("原生检查未完成");
 }
 internal static object Run(string installation,string directory,IList blocks){
  var clock=Stopwatch.StartNew();var timings=new Dictionary<string,long>();
  string libraries=Path.Combine(installation,"DNaviZero","DataAbsorber");
  if(IntPtr.Size!=4||FileVersionInfo.GetVersionInfo(Path.Combine(libraries,"DZDataABS_Workspace.dll")).FileVersion!="1.620.0.1")
   throw new InvalidOperationException("仅验证 GX Works2 1.620.0.1");
  string temporary=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"MITSUBISHI","SWnDN-GPPW2","Project","DZTempData",Process.GetCurrentProcess().Id.ToString());
  if(Directory.Exists(temporary))throw new InvalidOperationException("原生临时目录冲突");
  Directory.CreateDirectory(temporary);File.WriteAllText(Path.Combine(temporary,"gxworks2-mcp-owner.txt"),Path.GetFullPath(directory));
  File.WriteAllText(Path.Combine(directory,"native-temp-path.txt"),temporary);
  if(!SetDllDirectory(libraries))throw new InvalidOperationException("原生库目录失败");
  Check("CoInitialize",CoInitializeEx(IntPtr.Zero,2));int c;
  IntPtr nav=Create("0bf65a7e-5f7d-4c34-927a-a24ef88a7c8a","00020400-0000-0000-c000-000000000046");
  Check("Navigator.Initialize",Slot<Self>(nav,28)(nav));IntPtr app=Alloc(28),extra=B("");
  Marshal.StructureToPtr(new App{Kind=1,Language=B("SimpleChinese"),Page=936,ProjectPage=936,References=80000},app,false);
  Check("Application",Slot<AppCall>(nav,400)(nav,app,ref extra,out c),c);
  Marshal.WriteInt32(app,-1);Check("Support",Slot<Support>(nav,348)(nav,1,app,1));
  Check("ParameterVersion",Slot<TwoInt>(nav,360)(nav,59,0));
  IntPtr ws=Create("97de49af-71ee-4f89-b6c3-f8bc47b6f5ad","a5421505-a3a7-4614-8b3e-086afa94c770");
  VerifyModule(installation,"DZDataABS_Workspace.dll");
  IntPtr inside=Query(ws,"2a4da64f-0505-4b64-9414-e5c97594ba51");Check("SetNavigator",Slot<Pointer>(inside,16)(inside,nav,out c),c);
  // 仅绑定离线连接元数据，不调用任何通信、读写或 RUN/STOP 接口。
  IntPtr comm=Create("98651bdf-9fc8-4a3e-abd3-69892b52a82c","59313063-9d27-4f30-acb6-4e662fffa9ef"),ci=Query(comm,"a1b152dd-01e5-4764-893b-91ed0759ef35");
  Check("CommNavigator",Slot<Pointer>(ci,12)(ci,nav,out c),c);Check("CommWorkspace",Slot<Pointer>(ci,32)(ci,ws,out c),c);
  Check("SetComm",Slot<Pointer>(inside,36)(inside,comm,out c),c);Check("Display",Slot<IntCode>(ws,3556)(ws,1,out c),c);
  Check("MFCMode",Slot<IntCode>(ws,4288)(ws,1,out c),c);Check("Workspace.Initialize",Slot<Code>(ws,28)(ws,out c),c);
  IntPtr f;Check("Common",Slot<Function>(ws,32)(ws,0x10003,out f,out c),c);Check("OperationSetting",Slot<IntCall>(ws,4828)(ws,2));
  Check("Operations",Slot<Function>(ws,32)(ws,0x10000,out f,out c),c);IntPtr op=Query(f,"d4368a41-d713-4013-86f3-9ea1a3922930");
  VerifyModule(installation,"DZDataABS_ProjectOperation.dll");
  timings["initialize"]=clock.ElapsedMilliseconds;clock.Restart();
  string homePath=Path.Combine(directory,"workspace");if(Directory.Exists(homePath))throw new InvalidOperationException("隔离工作区已存在");
  Directory.CreateDirectory(homePath);IntPtr home=B(homePath+"\\"),workspace=B("isolated"),input=B(Path.Combine(directory,"input.gxw")),name;
  Check("ProjectName",Slot<FileName>(op,424)(op,input,out name,out c),c);string projectName=Text(name);
  if(String.IsNullOrEmpty(projectName)||projectName!=Path.GetFileName(projectName)||projectName=="."||projectName=="..")throw new InvalidOperationException("原生工程名称无效");
  Check("Import",Slot<Import>(op,416)(op,home,workspace,name,input,out c),c);
  timings["importOnly"]=clock.ElapsedMilliseconds;
  string imported=Path.Combine(homePath,"isolated",projectName)+"\\";
  if(!File.Exists(imported+"Project.gd2")||new FileInfo(imported+"Project.gd2").Length==0)Check("CreateProjectHash",Slot<Pointer>(op,448)(op,B(imported),out c),c);
  Attributes attributes;Check("Attributes",Slot<GetAttr>(op,408)(op,home,workspace,name,out attributes,out c),c);
  if(Text(attributes.Cpu)!="FX3U/FX3UC")throw new InvalidOperationException("原生 CPU 不匹配");
  Check("Controller",Slot<TwoInt>(nav,344)(nav,0x1fffff,1));IntPtr language=B("SimpleChinese"),cpu=B("FX3U/FX3UC");
  Check("Language",Slot<Cpu>(nav,76)(nav,ref language,1));Check("CPU",Slot<Cpu>(nav,40)(nav,ref cpu,1));
  IntPtr reserved=Alloc(48),parameter=Alloc(20);Marshal.StructureToPtr(new Project{Cpu=cpu,Name=B(""),Extra=B(""),Kind=3,Mode=-1},parameter,false);
  Check("Reserve",Slot<Reserve>(nav,288)(nav,IntPtr.Zero,parameter,reserved,out c),c);Check("Reserved",Slot<Pointer>(inside,12)(inside,reserved,out c),c);
  Id id;Check("Open",Slot<OpenCall>(op,340)(op,1,home,workspace,name,-1,16,0,out id,out c),c);
  timings["importOpen"]=clock.ElapsedMilliseconds;timings["openOnly"]=timings["importOpen"]-timings["importOnly"];clock.Restart();
  if(blocks!=null)Edit(ws,id,blocks);
  timings["edit"]=clock.ElapsedMilliseconds;clock.Restart();
  Check("Compiler",Slot<ProjectFunction>(ws,2560)(ws,id,0x10001,out f,out c),c);IntPtr compiler=Query(f,"99db1d02-f474-430d-bc1f-03abf0c7064b");
  VerifyModule(installation,"DZDataABS_CompilerAdapter.dll");
  Check("ChangeSFCProgram",Slot<Code>(compiler,156)(compiler,out c),c);var compileReports=Poll(compiler,168,false);
  timings["compile"]=clock.ElapsedMilliseconds;clock.Restart();
  Check("UpdateCode",Slot<IdCode>(ws,1736)(ws,id,out c),c);Id resources;
  Check("Resources",Slot<Collection>(ws,36)(ws,id,7,out resources,out c),c);var targets=Children(ws,resources);
  if(targets.Count!=1)throw new InvalidOperationException("目前仅验证单 MAIN 资源");
  Check("ResourceCompiler",Slot<ProjectFunction>(ws,2560)(ws,targets[0],0x10001,out f,out c),c);
  IntPtr resourceCompiler=Query(f,"99db1d02-f474-430d-bc1f-03abf0c7064b");
  Check("ProgramCheck",Slot<ProgramCheck>(resourceCompiler,180)(resourceCompiler,targets[0],-1,out c),c);var checkReports=Poll(resourceCompiler,40,true);
  timings["check"]=clock.ElapsedMilliseconds;clock.Restart();
  Check("Save",Slot<Save>(op,24)(op,home,workspace,name,0,id,0,out c),c);
  timings["saveOnly"]=clock.ElapsedMilliseconds;
  Check("FinalizeProjectHash",Slot<Pointer>(op,448)(op,B(imported),out c),c);
  timings["save"]=clock.ElapsedMilliseconds;timings["hashOnly"]=timings["save"]-timings["saveOnly"];clock.Restart();
  Check("Export",Slot<Export>(op,420)(op,home,workspace,name,B(Path.Combine(directory,"output.gxw")),3,0,0,out c),c);
  timings["export"]=clock.ElapsedMilliseconds;
  return new{compileStatus="accepted",checkStatus="accepted",errors=0,warnings=checkReports.FindAll(r=>(int)r["kind"]==3).Count,
             compileReports=compileReports,checkReports=checkReports,workspaceVersion="1.620.0.1",timingsMs=timings};
 }
}
