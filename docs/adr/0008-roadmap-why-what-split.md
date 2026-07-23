# Roadmap 按 Why/What 分层：README 讲愿景，issue 存可执行项

README 的 Roadmap 段原为功能点 checkbox 清单（What），与 GitHub issue tracker 的可执行工作项职责重叠：checkbox 不可被 agent 抓取、无 blocking edges、无验收标准，既无法驱动执行也容易腐烂。改为分层--README 只保留 **Why**（项目想做成什么样的终态愿景），**What**（具体做什么的可执行项）全部进 issue tracker。README Roadmap 段开头指向 issue tracker，明确 What 在那边跟踪。

这与 Matt Pocock 体系中 spec（做成什么样）与 tickets（做什么）的分层一致：README 充当持久化的愿景层，issue tracker 充当可抓取的工作队列。新增 roadmap 项时先问"是 Why 还是 What"--终态愿景进 README，可执行项进 issue。
