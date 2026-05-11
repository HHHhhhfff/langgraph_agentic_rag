```
下面是touch --help的结果，我希望你着重帮我讲讲里面touch命令各参数：各参数的作用，各参数使用格式等等。
Usage: touch [OPTION]... FILE...
Update the access and modification times of each FILE to the current time.

A FILE argument that does not exist is created empty, unless -c or -h
is supplied.

A FILE argument string of - is handled specially and causes touch to
change the times of the file associated with standard output.

Mandatory arguments to long options are mandatory for short options too.
  -a                     change only the access time
  -c, --no-create        do not create any files
  -d, --date=STRING      parse STRING and use it instead of current time
  -f                     (ignored)
  -h, --no-dereference   affect each symbolic link instead of any referenced
                         file (useful only on systems that can change the
                         timestamps of a symlink)
  -m                     change only the modification time
  -r, --reference=FILE   use this file's times instead of current time
  -t STAMP               use [[CC]YY]MMDDhhmm[.ss] instead of current time
      --time=WORD        change the specified time:
                           WORD is access, atime, or use: equivalent to -a
                           WORD is modify or mtime: equivalent to -m
      --help        display this help and exit
      --version     output version information and exit

Note that the -d and -t options accept different time-date formats.

GNU coreutils online help: <https://www.gnu.org/software/coreutils/>
Report any translation bugs to <https://translationproject.org/team/>
Full documentation <https://www.gnu.org/software/coreutils/touch>
or available locally via: info '(coreutils) touch invocation'
```

### touch --help :
```
Usage: touch [OPTION]... FILE...
Update the access and modification times of each FILE to the current time.

A FILE argument that does not exist is created empty, unless -c or -h
is supplied.

A FILE argument string of - is handled specially and causes touch to
change the times of the file associated with standard output.

Mandatory arguments to long options are mandatory for short options too.
  -a                     change only the access time
  -c, --no-create        do not create any files
  -d, --date=STRING      parse STRING and use it instead of current time
  -f                     (ignored)
  -h, --no-dereference   affect each symbolic link instead of any referenced
                         file (useful only on systems that can change the
                         timestamps of a symlink)
  -m                     change only the modification time
  -r, --reference=FILE   use this file's times instead of current time
  -t STAMP               use [[CC]YY]MMDDhhmm[.ss] instead of current time
      --time=WORD        change the specified time:
                           WORD is access, atime, or use: equivalent to -a
                           WORD is modify or mtime: equivalent to -m
      --help        display this help and exit
      --version     output version information and exit

Note that the -d and -t options accept different time-date formats.

GNU coreutils online help: <https://www.gnu.org/software/coreutils/>
Report any translation bugs to <https://translationproject.org/team/>
Full documentation <https://www.gnu.org/software/coreutils/touch>
or available locally via: info '(coreutils) touch invocation'
```


```
(1)touch 命令是一个多功能工具，用于创建新文件或更新现有文件的时间戳。
	1. 理解 touch 命令的用途和语法
	2. 使用 touch 命令创建新文件
	3. 使用 touch 命令修改文件时间戳

(2)touch命令的基础语法:
	touch [options] [file_name(s)]

(3)touch命令选项:
	- -a：更新文件的访问时间。
	- -m：更新文件的修改时间。
	- -d 或 --date=STRING：将访问和修改时间设置为指定的日期和时间（touch -d "时间字符串" file.txt）。
		支持的格式:
			touch -d "2025-01-01 12:00" file.txt
			touch -d "yesterday" file.txt
			touch -d "2 days ago" file.txt
			touch -d "next friday" file.txt
			touch -d "2025-01-01" file.txt
	- -t：用固定格式指定时间（示例：touch -t YYYYMMDDhhmm file.txt）
		格式说明:
			[[CC]YY]MMDDhhmm[.ss]
		示例:
			touch -t 202501011230.45 file.txt
	- -c 或 --no-create：如果文件不存在 → 不创建文件。如果存在 → 更新时间
	- -f：兼容历史系统，没有实际作用
	- -r 或 --reference=FILE(参考文件的文件名)： 参考文件时间
		示例:
			touch -r ref.txt file.txt
		作用:
			把 file.txt 的时间改成和 ref.txt 一样
	- --time=WORD：
		示例：
			touch --time=atime file.txt
			touch --time=mtime file.txt
		作用：
			按照指定模式更新文件访问时间/修改时间
		
	- -h, --no-dereference：修改符号链接本身，而不是它指向的文件
		示例：
			touch link.txt 
			如果 link.txt 是软链接：默认修改 → 目标文件
			
			touch -h link.txt
			符号链接本身
	
	[1]创建一个.sh文件:
		cd ~/project
		touch shTest01.sh
		
		输出: 无
	这里~表示home目录，即/home/hehuaifeng(用户名)
	
	[2]更新文件修改时间(mtime):
		touch -m new_file.txt
		
		输出: 无
	
	[3]更新文件访问时间(atime):
		touch -a existing_file.txt
		
		输出: 无
	
	[4]一次性创建多个文件
		touch file1.txt file2.txt file3.txt
		
		输出: 无
	
	[5]将访问时间和修改时间设置为特定的日期和时间:
		touch -d "2023-04-01 10:30:00" existing_file.txt
		
		输出: 无
	
	[6]更新文件访问时间和修改时间(如果文件已经存在):
		touch file.txt
		
		输出: 无
	不会覆盖而是更新访问和修改时间
	
	[7]使用固定格式指定文件的访问和修改时间(touch -t YYYYMMDDhhmm file.txt):
		touch -t 202501011230 file.txt
		
		输出: 无
	
	[8]特殊情况: FILE 是 -
		touch -
		修改标准输出对应的文件时间(几乎不用，了解即可)


(4)查看文件时间:
	[1]文件时间:
		|时间|含义|
		|atime|最后访问时间|
		|mtime|内容修改时间|
		|ctime|元数据修改时间|
	
	[2]查看: 
		stat shTest01.sh
		
		输出:
		  File: shTest01.sh
		  Size: 0               Blocks: 0          IO Block: 4096   regular empty file
		Device: 8,48    Inode: 14804       Links: 1
		Access: (0644/-rw-r--r--)  Uid: ( 1000/hehuaifeng)   Gid: ( 1000/hehuaifeng)
		Access: 2026-03-22 20:52:35.009360044 +0800
		Modify: 2026-03-22 20:52:35.009360044 +0800
		Change: 2026-03-22 20:52:35.009360044 +0800
		 Birth: 2026-03-22 20:52:35.009360044 +0800

(5)参数组合使用:
	[1]修改 mtime 为昨天:
		touch -m -d "yesterday" file.txt
	
	[2]只修改 atime 为指定时间:
		touch -a -d "2025-01-01" file.txt
	
	[3]批量修改:
		touch *.txt
	

(6)注意事项:
	touch只能创建文件，不能创建目录


(6)扩展:
	[1]touch + stat + find 这三个命令及综合使用
	
	[2]修改所有日志时间（防止被清理）:
		find . -name "*.log" -exec touch {} \;
	
	[3]修改7天前的文件时间:
		find . -mtime +7 -exec touch {} \;
	

```

## 介绍

在本实验中，我们将探索 Linux 的 `touch` 命令及其实际应用。`touch` 命令是一个多功能工具，用于创建新文件或更新现有文件的时间戳。我们将从理解 `touch` 命令的用途和语法开始，然后学习如何使用它创建新文件，最后探索如何修改文件的时间戳。

本实验涵盖以下步骤：

1. 理解 `touch` 命令的用途和语法
2. 使用 `touch` 命令创建新文件
3. 使用 `touch` 命令修改文件时间戳

本实验的内容侧重于 Linux 中的基本文件和目录操作，提供实际示例和逐步指导，帮助用户熟练使用 `touch` 命令管理文件和目录。

> [Linux 命令速查表](https://linux-commands.labex.io/)

## 理解 `touch` 命令的用途和语法

在这一步中，我们将探索 Linux 中 `touch` 命令的用途和语法。`touch` 命令是一个多功能工具，用于创建新文件或更新现有文件的时间戳。

`touch` 命令的基本语法如下：

```
touch [options] [file_name(s)]
```

以下是一些常用的 `touch` 命令选项：

- `-a`：更新文件的访问时间。
- `-m`：更新文件的修改时间。
- `-d` 或 `-t`：将访问和修改时间设置为指定的日期和时间。
- `-c` 或 `-f`：如果文件不存在，则创建文件，且不显示错误消息。

让我们从使用 `touch` 命令创建一个新文件开始：

```
cd ~/project
touch new_file.txt
```

示例输出：

```

```

`touch` 命令在 `~/project` 目录中创建了一个名为 `new_file.txt` 的新文件。

接下来，让我们更新文件的修改时间：

```
touch -m new_file.txt
```

示例输出：

```

```

`touch -m` 命令更新了 `new_file.txt` 文件的修改时间。

## 使用 `touch` 命令创建新文件

在这一步中，我们将学习如何使用 `touch` 命令以多种方式创建新文件。

首先，让我们创建一个单独的文件：

```
cd ~/project
touch new_file.txt
```

示例输出：

```

```

`touch new_file.txt` 命令在 `~/project` 目录中创建了一个名为 `new_file.txt` 的新文件。

接下来，让我们一次性创建多个文件：

```
touch file1.txt file2.txt file3.txt
```

示例输出：

```

```

`touch file1.txt file2.txt file3.txt` 命令在 `~/project` 目录中创建了三个新文件：`file1.txt`、`file2.txt` 和 `file3.txt`。

你还可以使用通配符创建具有相似命名模式的多个文件：

```
touch *.md
```

示例输出：

```

```

`touch *.md` 命令在 `~/project` 目录中创建了所有扩展名为 `.md` 的文件。

## 使用 `touch` 命令修改文件时间戳

在这一步中，我们将学习如何使用 `touch` 命令修改文件的访问时间和修改时间。

首先，让我们创建一个新文件：

```
cd ~/project
touch existing_file.txt
```

现在，让我们更新文件的访问时间：

```
touch -a existing_file.txt
```

示例输出：

```

```

`touch -a` 命令更新了 `existing_file.txt` 文件的访问时间。

接下来，让我们更新文件的修改时间：

```
touch -m existing_file.txt
```

示例输出：

```

```

`touch -m` 命令更新了 `existing_file.txt` 文件的修改时间。

你还可以使用 `-d` 或 `-t` 选项将访问和修改时间设置为特定的日期和时间：

```
touch -d "2023-04-01 10:30:00" existing_file.txt
```

示例输出：

```

```

`touch -d "2023-04-01 10:30:00"` 命令将 `existing_file.txt` 文件的访问和修改时间设置为 2023 年 4 月 1 日上午 10:30。

## 总结

在本实验中，我们学习了 Linux 中 `touch` 命令的用途和语法，包括常见的选项，例如 `-a` 用于更新访问时间，`-m` 用于更新修改时间，以及 `-d` 或 `-t` 用于设置特定的访问和修改时间。我们还练习了使用 `touch` 命令创建新文件，包括单独创建和批量创建，以及使用通配符创建具有相似命名模式的多个文件。最后，我们探索了如何使用 `touch` 命令修改文件时间戳。

> [Linux 命令速查表](https://linux-commands.labex.io/)

## 您可能感兴趣的其他 Linux 教程

- [文件打包与压缩](https://labex.io/zh/tutorials/linux-file-packaging-and-compression-385413)
- [Bash 脚本循环](https://labex.io/zh/tutorials/shell-bash-scripting-loops-388816)
- [Linux cd 命令实用示例](https://labex.io/zh/tutorials/linux-linux-cd-command-with-practical-examples-422591)
- [如何在 Linux 中搜索多种文件类型](https://labex.io/zh/tutorials/linux-how-to-search-multiple-file-types-in-linux-422366)










`touch` 是一个**非常简单但非常常用**的 Linux 命令，看起来功能不多，但在实际开发和运维中使用频率很高。

我给你做一个**系统性讲解**，包括：

- 基本作用
    
- 工作原理
    
- 常用参数
    
- 实际场景
    
- 常见误区
    

---

# 一、touch 的核心作用

`touch` 有两个主要用途：

> **1️⃣ 创建空文件**  
> **2️⃣ 修改文件的时间戳（timestamp）**

---

## 1. 创建文件

```bash
touch file.txt
```

如果文件不存在：

```text
创建一个空文件 file.txt
```

---

## 2. 修改时间

如果文件已经存在：

```bash
touch file.txt
```

不会修改内容，只会更新：

```text
访问时间（atime）
修改时间（mtime）
```

---

# 二、文件时间的概念（很重要）

Linux 文件有三种时间：

|时间|含义|
|---|---|
|atime|最后访问时间|
|mtime|内容修改时间|
|ctime|元数据修改时间|

查看：

```bash
stat file.txt
```

---

## touch 默认行为

```text
更新 atime 和 mtime
```

---

# 三、基本语法

```bash
touch [选项] 文件...
```

可以一次操作多个文件：

```bash
touch a.txt b.txt c.txt
```

---

# 四、常用参数（重点）

---

## 1. `-c`（不创建文件）

```bash
touch -c file.txt
```

含义：

```text
如果文件不存在 → 不创建
如果存在 → 更新时间
```

---

## 2. `-a`（只修改访问时间）

```bash
touch -a file.txt
```

只更新：

```text
atime
```

---

## 3. `-m`（只修改修改时间）

```bash
touch -m file.txt
```

只更新：

```text
mtime
```

---

## 4. `-t`（指定时间）

格式：

```bash
touch -t YYYYMMDDhhmm file.txt
```

示例：

```bash
touch -t 202501011200 file.txt
```

表示：

```text
2025-01-01 12:00
```

---

## 5. `-d`（使用可读时间）

更常用：

```bash
touch -d "2025-01-01 12:00" file.txt
```

或者：

```bash
touch -d "yesterday" file.txt
```

---

## 6. `-r`（参考文件时间）

```bash
touch -r ref.txt file.txt
```

意思：

```text
把 file.txt 的时间改成和 ref.txt 一样
```

---

# 五、常见使用场景

---

## 1. 创建文件

```bash
touch main.py
```

---

## 2. 批量创建

```bash
touch file{1..5}.txt
```

生成：

```text
file1.txt file2.txt ... file5.txt
```

---

## 3. 更新时间戳（触发构建）

在开发中常见：

```bash
touch file.c
```

用于：

```text
触发 make 重新编译
```

---

## 4. 修改文件时间（伪造时间）

```bash
touch -d "2020-01-01" file.txt
```

---

## 5. 和 find 配合

查找最近修改文件：

```bash
find . -mtime -1
```

配合：

```bash
touch file.txt
```

可以控制测试。

---

# 六、一个非常重要的理解

## touch 并不会“写入内容”

例如：

```bash
touch file.txt
```

文件内容：

```text
空
```

如果你想写内容：

```bash
echo "hello" > file.txt
```

---

# 七、touch vs 其他命令

|命令|作用|
|---|---|
|touch|创建空文件 / 更新时间|
|echo >|写入内容|
|cp|复制文件|
|mv|移动文件|

---

# 八、常见误区

---

## ❌ 误区1：touch 会写入内容

错：

```text
touch 不写任何内容
```

---

## ❌ 误区2：touch 会覆盖文件

错：

```text
touch 不会删除原内容
```

---

## ❌ 误区3：touch 只能创建文件

错：

```text
touch 更常用于“更新时间”
```

---

# 九、进阶：时间在系统中的作用

很多系统依赖时间：

- make 构建系统
    
- 日志分析
    
- 缓存机制
    
- 增量备份
    

例如：

```bash
make
```

依赖：

```text
mtime（修改时间）
```

---

# 十、总结

`touch` 的本质：

```text
1️⃣ 创建空文件
2️⃣ 修改时间戳
```

最常用命令：

```bash
touch file.txt
touch -d "2025-01-01" file.txt
touch -r ref.txt file.txt
```

---

# 十一、一句话记忆

```text
touch = 轻触文件 → 更新时间 / 创建空文件
```

---

如果你愿意，我可以继续给你讲一个非常实用的组合：

👉 `touch + find + xargs`

比如：

```bash
find . -type f -name "*.log" -mtime +7 -exec touch {} \;
```

这种命令在日志维护、自动清理中非常常见。




很好，这一步你已经开始直接读 `man/--help` 文档了，这是非常正确的学习方式 👍  
我帮你把这些参数 **按逻辑分组 + 逐条解释 + 配示例**，让你不只是“看懂”，而是“会用”。

---

# 一、先建立一个整体框架

`touch` 的所有参数，本质上可以分为 **4大类**：

```text
1️⃣ 控制修改哪种时间（atime / mtime）
2️⃣ 控制时间来源（当前时间 / 指定时间 / 参考文件）
3️⃣ 控制是否创建文件
4️⃣ 特殊行为（符号链接等）
```

---

# 二、默认行为（非常重要）

```bash
touch file.txt
```

默认做两件事：

```text
1 修改 atime（访问时间）
2 修改 mtime（修改时间）
3 如果文件不存在 → 创建空文件
```

---

# 三、参数详解（逐个讲）

---

# 1️⃣ `-a`：只修改访问时间

```bash
touch -a file.txt
```

作用：

```text
只修改 atime（访问时间）
不修改 mtime
```

---

## 示例

```bash
touch file.txt
stat file.txt
```

再执行：

```bash
touch -a file.txt
```

效果：

```text
atime 更新
mtime 不变
```

---

# 2️⃣ `-m`：只修改修改时间

```bash
touch -m file.txt
```

作用：

```text
只修改 mtime（内容修改时间）
```

---

## 对比

|参数|修改|
|---|---|
|-a|atime|
|-m|mtime|
|无参数|两者都改|

---

# 3️⃣ `-c, --no-create`：不创建文件

```bash
touch -c file.txt
```

作用：

```text
文件不存在 → 不创建
文件存在 → 更新时间
```

---

## 示例

```bash
touch -c not_exist.txt
```

结果：

```text
不会创建文件
```

---

# 4️⃣ `-d, --date=STRING`：指定时间（最常用）

```bash
touch -d "时间字符串" file.txt
```

作用：

```text
使用你给定的时间，而不是当前时间
```

---

## 示例

```bash
touch -d "2025-01-01 12:00" file.txt
```

---

## 支持的格式（很强大）

```bash
touch -d "yesterday" file.txt
touch -d "2 days ago" file.txt
touch -d "next friday" file.txt
touch -d "2025-01-01" file.txt
```

---

# 5️⃣ `-t STAMP`：指定时间（固定格式）

```bash
touch -t YYYYMMDDhhmm file.txt
```

作用：

```text
用固定格式指定时间
```

---

## 示例

```bash
touch -t 202501011230 file.txt
```

表示：

```text
2025-01-01 12:30
```

---

## 格式说明

```text
[[CC]YY]MMDDhhmm[.ss]
```

例如：

```bash
touch -t 202501011230.45 file.txt
```

---

## ⚠️ 和 `-d` 区别

|参数|特点|
|---|---|
|-d|灵活（推荐）|
|-t|格式严格|

---

# 6️⃣ `-r, --reference=FILE`：参考文件时间

```bash
touch -r ref.txt file.txt
```

作用：

```text
把 file.txt 的时间改成和 ref.txt 一样
```

---

## 示例

```bash
touch a.txt
sleep 2
touch b.txt

touch -r a.txt b.txt
```

结果：

```text
b.txt 的时间 = a.txt
```

---

# 7️⃣ `--time=WORD`

等价参数：

```bash
touch --time=atime file.txt
touch --time=mtime file.txt
```

等价于：

```text
--time=atime → -a
--time=mtime → -m
```

---

## 示例

```bash
touch --time=atime file.txt
```

---

# 8️⃣ `-h, --no-dereference`（符号链接）

作用：

```text
修改符号链接本身，而不是它指向的文件
```

---

## 默认行为

```bash
touch link.txt
```

如果 `link.txt` 是软链接：

```text
默认修改 → 目标文件
```

---

## 使用 `-h`

```bash
touch -h link.txt
```

修改：

```text
符号链接本身
```

---

## 示例

```bash
ln -s real.txt link.txt
touch -h link.txt
```

---

# 9️⃣ `-f`（被忽略）

```text
-f (ignored)
```

说明：

```text
兼容历史系统，没有实际作用
```

👉 可以忽略

---

# 10️⃣ 特殊情况：FILE 是 `-`

```bash
touch -
```

作用：

```text
修改标准输出对应的文件时间
```

👉 几乎不用，了解即可

---

# 四、参数组合使用

---

## 示例1：修改 mtime 为昨天

```bash
touch -m -d "yesterday" file.txt
```

---

## 示例2：只修改 atime 为指定时间

```bash
touch -a -d "2025-01-01" file.txt
```

---

## 示例3：批量修改

```bash
touch *.txt
```

---

# 五、一个非常实用的运维例子

## 修改所有日志时间（防止被清理）

```bash
find . -name "*.log" -exec touch {} \;
```

---

## 修改7天前的文件时间

```bash
find . -mtime +7 -exec touch {} \;
```

---

# 六、总结（参数速查表）

|参数|作用|
|---|---|
|-a|只改访问时间|
|-m|只改修改时间|
|-c|不创建文件|
|-d|指定时间（推荐）|
|-t|指定时间（固定格式）|
|-r|参考文件时间|
|--time|指定修改哪种时间|
|-h|作用于符号链接|

---

# 七、一句话理解 touch

```text
touch = 改时间（如果没有就顺便创建文件）
```

---

# 八、你现在应该掌握的核心

重点记住这 3 个：

```bash
touch file.txt
touch -d "2025-01-01" file.txt
touch -r ref.txt file.txt
```

这已经覆盖 90% 使用场景。

---

如果你愿意，我可以下一步帮你做一个：

👉 **“touch + stat + find 综合练习”**

帮你彻底搞懂 Linux 文件时间系统（这是很多人卡住的点）。