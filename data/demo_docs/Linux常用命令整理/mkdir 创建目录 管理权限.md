### mkdir --help:
```
Usage: mkdir [OPTION]... DIRECTORY...
Create the DIRECTORY(ies), if they do not already exist.

Mandatory arguments to long options are mandatory for short options too.
  -m, --mode=MODE   set file mode (as in chmod), not a=rwx - umask
  -p, --parents     no error if existing, make parent directories as needed,
                    with their file modes unaffected by any -m option.
  -v, --verbose     print a message for each created directory
  -Z                   set SELinux security context of each created directory
                         to the default type
      --context[=CTX]  like -Z, or if CTX is specified then set the SELinux
                         or SMACK security context to CTX
      --help        display this help and exit
      --version     output version information and exit

GNU coreutils online help: <https://www.gnu.org/software/coreutils/>
Report any translation bugs to <https://translationproject.org/team/>
Full documentation <https://www.gnu.org/software/coreutils/mkdir>
or available locally via: info '(coreutils) mkdir invocation'
```


```
下面是mkdir --help的结果，我希望你根据该内容讲讲mkdir这个命令。
要求:
(1)着重讲讲里面mkdir命令参数的相关内容

Usage: mkdir [OPTION]... DIRECTORY...
Create the DIRECTORY(ies), if they do not already exist.

Mandatory arguments to long options are mandatory for short options too.
  -m, --mode=MODE   set file mode (as in chmod), not a=rwx - umask
  -p, --parents     no error if existing, make parent directories as needed,
                    with their file modes unaffected by any -m option.
  -v, --verbose     print a message for each created directory
  -Z                   set SELinux security context of each created directory
                         to the default type
      --context[=CTX]  like -Z, or if CTX is specified then set the SELinux
                         or SMACK security context to CTX
      --help        display this help and exit
      --version     output version information and exit

GNU coreutils online help: <https://www.gnu.org/software/coreutils/>
Report any translation bugs to <https://translationproject.org/team/>
Full documentation <https://www.gnu.org/software/coreutils/mkdir>
or available locally via: info '(coreutils) mkdir invocation'

```


```
(1)mkdir主要作用:
	创建目录 和 管理权限

(2)mkdir基本格式:
	mkdir [参数] file(s)
	
	示例1:
		mkdir test
	创建test目录(在当前路径下)
	
	示例2:
		mkdir dir1 dir2 dir3
	创建多个目录(在当前路径下)

(3)mkdir参数大概分类:
	1️⃣ 权限控制（-m）
	2️⃣ 递归创建（-p）
	3️⃣ 输出控制（-v）
	4️⃣ 安全/安全上下文（-Z, --context）


(4)mkdir参数详解:
	[1]-p, --parents: 递归创建多级目录
		示例:
			mkdir -p parent/child/grandchild 
			ls -R 
		输出: 
			.:
			'*.md'  '*.txt'  'abc*.txt'   empty1   file1.txt   parent   shTest01.sh
			
			./empty1:
			empty2
			
			./empty1/empty2:
			
			./parent:
			child
			
			./parent/child:
			grandchild
			
			./parent/child/grandchild:
		
		
		作用:
			1 如果父目录不存在 → 自动创建
			2 如果目录已存在 → 不报错
		
		若不加-p创建多级目录会报错:
			mkdir a/b/c
		报错：No such file or directory
		
	注意: mkdir不加这个参数，默认不会创建父目录
	
	
	[2]-m, --mode=MODE: 权限设置
		示例:
			mkdir -m 755 test
		效果: 创建test目录，并指定test目录的权限
		
		作用:
			创建目录时直接指定权限
		大概格式: mkdir -m MODE(权限模式) dir
		
	注意: -m 不受 umask 影响(umask是什么?)
	
	
	[3]-v, --verbose: 显示创建过程
		示例:
			mkdir -v test
		输出: mkdir: created directory 'test'
		
		作用:
			显示创建过程
	
	
	[4]-Z: SELinux 相关(了解即可)
		作用:
			设置默认 SELinux 安全上下文
		
		这个参数：
			- 这在普通 Linux 学习中 基本用不到
			- 只在 启用了 SELinux 的系统（如 CentOS） 中有用
	
	
	[5]--context[=CTX]: 指定 SELinux / SMACK 安全上下文
		作用:
			指定 SELinux / SMACK 安全上下文
		
	说明: 了解即可


(5)mkdir注意事项:
	
	- 默认不会创建父目录:
		mkdir a/b
	如果 a 不存在 → 报错
	
	
	- 默认不能重复创建
		mkdir test
		mkdir test
	结果会报错: File exists
		可以:
			mkdir test 
			mkdir -p test
	用 -p 可以避免报错(只是不报错而已，mkdir -p test不会多创建一个目录test)




(6)常见组合:
	[1]mkdir -pv a/b/c



(7)mkdir + 权限:
	[1]目录默认权限来源:
		目录权限 = 777 - umask
		
	规则:
		查看 umask: umask
		输出示例: 0022
		最终目录默认权限: 777 - 022 = 755
		
	示例(基于umask为0022):
		mkdir test
		ls -ld test
	输出: drwxr-xr-x
		
		
	使用 -m 绕过 umask: 
		mkdir -m 700 test
	test文件权限: drwx------



(8)mkdir 和 touch 区别:
	|mkdir|创建目录|
	|touch|创建文件|







(10)扩展点:
	[1]mkdir + touch + rm + cp + mv综合使用 ---- Linux 文件/目录操作体系


```


## 介绍

在本实验中，你将学习如何使用 Linux 的 `mkdir` 命令创建目录并管理权限。实验内容涵盖创建单个或多个目录、使用 `-p` 选项创建嵌套目录，以及使用 `mkdir` 命令管理权限。内容包含实际示例和逐步指导，帮助你在 Linux 环境中熟练掌握基本的文件和目录操作。

> [Linux 命令速查表](https://linux-commands.labex.io/)

## 使用 mkdir 命令创建目录

在这一步中，你将学习如何在 Linux 中使用 `mkdir` 命令创建目录。

`mkdir` 命令用于创建新目录。你可以一次创建一个目录或多个目录。

要创建一个新目录，请使用以下语法：

```
mkdir directory_name
```

示例：

```
$ mkdir mydir
$ ls
mydir
```

在上面的示例中，我们使用 `mkdir` 命令创建了一个名为 `mydir` 的新目录。你可以通过运行 `ls` 命令来验证目录是否已创建。

你也可以通过提供多个目录名称（用空格分隔）来一次创建多个目录：

```
mkdir dir1 dir2 dir3
```

示例输出：

```
$ mkdir dir1 dir2 dir3
$ ls
dir1  dir2  dir3  mydir
```

现在，让我们创建一个多级目录结构：

```
mkdir -p parent/child/grandchild
```

`mkdir` 命令中的 `-p` 选项允许你在一条命令中创建整个目录结构，包括任何必要的父目录。

示例输出：

```
$ mkdir -p parent/child/grandchild
$ ls -R
.:
child  parent

./parent:
child

./parent/child:
grandchild
```

如你所见，`mkdir -p` 命令一步创建了 `parent`、`child` 和 `grandchild` 目录。

## 使用 mkdir -p 创建嵌套目录

在这一步中，你将学习如何在 Linux 中使用 `mkdir -p` 命令创建嵌套目录。

`mkdir -p` 命令允许你在一条命令中创建多级目录结构。当你需要同时创建一个目录及其父目录时，这非常有用。

让我们创建一个嵌套目录结构：

```
mkdir -p projects/web-app/src/components
```

示例输出：

```
$ mkdir -p projects/web-app/src/components
$ ls -R
projects

./projects:
web-app

./projects/web-app:
src

./projects/web-app/src:
components
```

如你所见，`mkdir -p` 命令创建了整个目录结构，包括 `projects`、`web-app`、`src` 和 `components` 目录。

现在，让我们创建另一个嵌套目录结构：

```
mkdir -p documents/reports/2023/q1
```

示例输出：

```
$ mkdir -p documents/reports/2023/q1
$ ls -R
documents  projects

./documents:
reports

./documents/reports:
2023

./documents/reports/2023:
q1

./projects:
web-app
```

`mkdir -p` 命令允许你在一步中创建整个目录结构，包括 `documents`、`reports`、`2023` 和 `q1` 目录。

## 使用 mkdir 管理权限

在这一步中，你将学习如何在 Linux 中使用 `mkdir` 命令创建目录时管理权限。

默认情况下，当你使用 `mkdir` 创建新目录时，目录会继承父目录的权限。然而，你也可以在创建目录时显式指定权限。

要创建一个具有特定权限的新目录，可以使用 `-m` 选项，后跟权限模式：

```
mkdir -m 755 my_dir
```

在上面的示例中，我们创建了一个名为 `my_dir` 的新目录，权限设置为 `755`（所有者具有读、写和执行权限；组和其他用户具有读和执行权限）。

你也可以使用符号权限而不是数字模式：

```
mkdir -m u=rwx,g=rx,o=rx my_dir
```

这条命令创建了 `my_dir` 目录，其权限与上一个示例相同，但使用了符号表示法。

让我们创建一个具有不同权限的目录：

```
mkdir -m 700 secret_dir
```

这将创建一个名为 `secret_dir` 的新目录，权限设置为 `700`（所有者具有读、写和执行权限；组和其他用户无访问权限）。

你可以使用 `ls -l` 命令验证目录的权限：

```
$ ls -l
total 8
drwxr-xr-x 2 labex labex 4096 Apr 12 12:34 my_dir
drwx------ 2 labex labex 4096 Apr 12 12:35 secret_dir
```

如你所见，`my_dir` 目录的权限为 `755`，而 `secret_dir` 目录的权限为 `700`。

## 总结

在本实验中，你学习了如何在 Linux 中使用 `mkdir` 命令创建目录。你可以一次创建单个目录或多个目录，还可以使用 `-p` 选项创建嵌套目录。此外，你还学习了在创建目录时如何管理权限。本实验的关键要点包括 `mkdir` 命令的各种用例，例如创建目录、创建嵌套目录以及管理权限。

> [Linux 命令速查表](https://linux-commands.labex.io/)

## 您可能感兴趣的其他 Linux 教程

- [文件与目录操作](https://labex.io/zh/tutorials/linux-file-and-directory-operations-17997)
- [Linux mkdir 命令：创建目录](https://labex.io/zh/tutorials/linux-linux-mkdir-command-directory-creating-209739)
- [Linux 环境变量](https://labex.io/zh/tutorials/linux-environment-variables-in-linux-385274)
- [Linux mdel 命令实战示例](https://labex.io/zh/tutorials/linux-linux-mdel-command-with-practical-examples-422795)
- [Linux mdir 命令实战示例](https://labex.io/zh/tutorials/linux-linux-mdir-command-with-practical-examples-422797)
- [如何优化 Linux 命令路径管理](https://labex.io/zh/tutorials/linux-how-to-optimize-linux-command-path-management-417381)
- [如何优化 Neofetch 以提升性能和可用性](https://labex.io/zh/tutorials/linux-how-to-optimize-neofetch-for-enhanced-performance-and-usability-417376)
- [在 Linux 中自定义“tail”命令的输出](https://labex.io/zh/tutorials/linux-customize-the-tail-command-output-in-linux-417364)
- [如何理解和分析 Linux 日志文件](https://labex.io/zh/tutorials/linux-how-to-understand-and-analyze-linux-log-files-417365)
- [如何在 Linux 中使用 rm 命令删除文件](https://labex.io/zh/tutorials/linux-how-to-delete-a-file-using-the-rm-command-in-linux-417366)




















很好，这一步你已经在系统地学习 Linux 基础命令 👍。  
`mkdir` 看似简单，但它涉及 **权限、目录层级、umask** 等关键概念。

我会按照你要求重点讲：

> **参数 + 使用格式 + 行为细节 + 示例**

---

# 一、mkdir 的核心作用

```bash
mkdir DIRECTORY...
```

作用：

> **创建目录（directory）**

---

## 基本示例

```bash
mkdir test
```

效果：

```text
创建目录 test/
```

---

## 创建多个目录

```bash
mkdir dir1 dir2 dir3
```

---

# 二、参数整体分类

根据 `--help`，参数可以分为：

```text
1️⃣ 权限控制（-m）
2️⃣ 递归创建（-p）
3️⃣ 输出控制（-v）
4️⃣ 安全/安全上下文（-Z, --context）
```

---

# 三、重点参数详解

---

# 1️⃣ `-p, --parents`（最重要参数之一）

```bash
mkdir -p a/b/c
```

作用：

```text
1 如果父目录不存在 → 自动创建
2 如果目录已存在 → 不报错
```

---

## ❗ 不加 -p 的行为

```bash
mkdir a/b/c
```

如果 `a` 不存在：

```text
报错：
No such file or directory
```

---

## 加 -p 后

```bash
mkdir -p a/b/c
```

效果：

```text
创建：
a/
a/b/
a/b/c/
```

---

## 再执行一次

```bash
mkdir -p a/b/c
```

👉 不报错（幂等）

---

## 实际使用场景

```bash
mkdir -p /var/log/myapp/
```

👉 运维、部署中非常常见

---

# 2️⃣ `-m, --mode=MODE`（权限设置）

```bash
mkdir -m MODE dir
```

作用：

> **创建目录时直接指定权限**

---

## 示例

```bash
mkdir -m 755 test
```

等价于：

```bash
mkdir test
chmod 755 test
```

---

## 权限解释

```text
755 → rwxr-xr-x
```

---

## ⚠️ 关键点（help 里提到）

```text
not a=rwx - umask
```

意思是：

👉 `-m` **不受 umask 影响**

---

## 对比

### 默认创建

```bash
mkdir test
```

权限：

```text
777 - umask
```

---

### 使用 -m

```bash
mkdir -m 777 test
```

权限：

```text
严格等于 777
```

---

# 3️⃣ `-v, --verbose`（显示过程）

```bash
mkdir -v dir
```

作用：

```text
显示创建过程
```

---

## 示例

```bash
mkdir -v test
```

输出：

```text
mkdir: created directory 'test'
```

---

## 常见组合

```bash
mkdir -pv a/b/c
```

输出：

```text
created directory 'a'
created directory 'a/b'
created directory 'a/b/c'
```

---

# 4️⃣ `-Z`（SELinux 相关）

```bash
mkdir -Z dir
```

作用：

```text
设置默认 SELinux 安全上下文
```

---

## 说明

这个参数：

- 在普通 Linux 学习中 **基本用不到**
    
- 只在 **启用了 SELinux 的系统（如 CentOS）** 中有用
    

---

# 5️⃣ `--context[=CTX]`

```bash
mkdir --context=CTX dir
```

作用：

```text
指定 SELinux / SMACK 安全上下文
```

---

## 示例（了解即可）

```bash
mkdir --context=system_u:object_r:httpd_sys_content_t:s0 dir
```

---

## 总结

👉 普通用户可以忽略：

```text
-Z
--context
```

---

# 四、mkdir 的行为细节（重要）

---

## 1️⃣ 默认不会创建父目录

```bash
mkdir a/b
```

👉 如果 `a` 不存在 → 报错

---

## 2️⃣ 默认不能重复创建

```bash
mkdir test
mkdir test
```

👉 报错：

```text
File exists
```

---

## 3️⃣ 用 -p 可以避免报错

```bash
mkdir -p test
```

👉 不报错

---

# 五、mkdir + 权限（深入理解）

---

## 默认权限来源

```text
目录权限 = 777 - umask
```

---

## 查看 umask

```bash
umask
```

例如：

```text
0022
```

那么：

```text
777 - 022 = 755
```

---

## 示例

```bash
mkdir test
ls -ld test
```

输出：

```text
drwxr-xr-x
```

---

## 使用 -m 绕过 umask

```bash
mkdir -m 700 test
```

权限：

```text
drwx------
```

---

# 六、常见实战场景

---

## 1️⃣ 创建多级目录

```bash
mkdir -p project/src/utils
```

---

## 2️⃣ 创建带权限目录

```bash
mkdir -m 700 private_dir
```

---

## 3️⃣ 批量创建目录

```bash
mkdir dir{1..5}
```

生成：

```text
dir1 dir2 dir3 dir4 dir5
```

---

## 4️⃣ 一边创建一边查看

```bash
mkdir -pv a/b/c/d
```

---

# 七、mkdir vs touch

|命令|作用|
|---|---|
|mkdir|创建目录|
|touch|创建文件|

---

# 八、常见错误

---

## ❌ 忘记 -p

```bash
mkdir a/b/c
```

👉 报错

---

## ❌ 权限问题

```bash
mkdir /root/test
```

👉 普通用户会报：

```text
Permission denied
```

---

# 九、推荐使用习惯

---

## ✅ 最安全写法

```bash
mkdir -p dir
```

---

## ✅ 开发/部署常用

```bash
mkdir -p /app/logs /app/config /app/tmp
```

---

# 十、总结（参数速查表）

|参数|作用|
|---|---|
|-p|递归创建目录（最常用）|
|-m|指定权限|
|-v|显示创建过程|
|-Z|SELinux 默认上下文|
|--context|指定安全上下文|

---

# 十一、一句话总结

```text
mkdir = 创建目录（-p 递归，-m 控权限）
```

---

# 十二、你现在应该重点掌握

最核心的两个：

```bash
mkdir dir
mkdir -p dir/subdir
```

进阶一点：

```bash
mkdir -p -m 755 dir
```

---

如果你愿意，我可以下一步帮你把这几个命令串起来讲：

```text
mkdir + touch + rm + cp + mv
```

👉 组成完整的 **Linux 文件/目录操作体系**（非常关键的一套知识）。