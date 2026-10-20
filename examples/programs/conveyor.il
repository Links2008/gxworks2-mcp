LD X0
OR Y0
ANI X1
OUT Y0      // 电机自锁：X0 启动、X1 停止
LD X0
MPS
ANDP M0
OUT Y10     // 启动瞬间的脉冲输出
MPP
AND X2
OUT Y11     // 运行中且 X2 有效
LD X3
ANI Y0
OUT M20     // 停止状态标志
LD M20
OUT Y12
END
