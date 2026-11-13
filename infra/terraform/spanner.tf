# Identity index. Spanner gives the read-compare-write of identity resolution a
# serializable transaction at population scale, with no hot single writer.
resource "google_spanner_instance" "mpi" {
  name             = "${local.name}-mpi"
  config           = "regional-${var.region}"
  display_name     = "${local.name} identity"
  processing_units = var.spanner_processing_units
  labels           = local.labels
  depends_on       = [google_project_service.enabled]
}

resource "google_spanner_database" "mpi" {
  instance                 = google_spanner_instance.mpi.name
  name                     = "mpi"
  version_retention_period = "7d"
  deletion_protection      = true

  encryption_config {
    kms_key_name = google_kms_crypto_key.store["spanner"].id
  }

  ddl = [
    <<-SQL
      CREATE TABLE Persons (
        Eid STRING(32) NOT NULL,
        CreatedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
      ) PRIMARY KEY (Eid)
    SQL
    ,
    <<-SQL
      CREATE TABLE SourceRecords (
        Eid STRING(32) NOT NULL,
        SourceSystem STRING(64) NOT NULL,
        IdSystem STRING(128) NOT NULL,
        IdValue STRING(128) NOT NULL,
        Demographics JSON NOT NULL,
        LinkedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
      ) PRIMARY KEY (Eid, SourceSystem, IdSystem, IdValue),
        INTERLEAVE IN PARENT Persons ON DELETE CASCADE
    SQL
    ,
    "CREATE UNIQUE INDEX SourceRecordsByIdentifier ON SourceRecords (IdSystem, IdValue)",
    <<-SQL
      CREATE TABLE BlockingKeys (
        BlockKey STRING(128) NOT NULL,
        Eid STRING(32) NOT NULL,
      ) PRIMARY KEY (BlockKey, Eid)
    SQL
    ,
    <<-SQL
      CREATE TABLE ReviewTasks (
        TaskId STRING(32) NOT NULL,
        NewEid STRING(32) NOT NULL,
        CandidateEid STRING(32) NOT NULL,
        Score FLOAT64 NOT NULL,
        Reason STRING(MAX),
        Status STRING(16) NOT NULL,
        CreatedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
        DecidedBy STRING(128),
      ) PRIMARY KEY (TaskId)
    SQL
    ,
  ]

  depends_on = [google_kms_crypto_key_iam_member.service_agent]
}
