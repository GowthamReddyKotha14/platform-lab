variable "cluster_name" {
  description = "Name of the local kind cluster"
  type        = string
  default     = "platform-lab"
}

variable "workers" {
  description = "Worker nodes (2 lets the node-failure demo move pods to a surviving node)"
  type        = number
  default     = 2
}

variable "repo_url" {
  description = "Git repo Argo CD watches. Everything under gitops/ is installed from here."
  type        = string
  default     = "https://github.com/GowthamReddyKotha14/platform-lab.git"
}

variable "revision" {
  description = "Branch Argo CD follows"
  type        = string
  default     = "main"
}
