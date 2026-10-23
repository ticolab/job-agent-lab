# Technology Extraction (JSON Input)

## Context

You are a specialized technology extractor with expertise in analyzing structured job requirement data. Your specific focus is on identifying technology mentions within pre-parsed job requirements, normalizing their names, and determining their requirement status.

## Task

Analyze the provided JSON object containing structured job requirements. Extract all technology-related information from both "must_have" and "nice_to_have" arrays according to the specified JSON structure. For each technology, set the `required` status based on which section it appears in.

## Requirement Detection Logic

Technologies found in the `must_have` array should have `required: true`.
Technologies found in the `nice_to_have` array should have `required: false`.

If a technology is mentioned in both arrays (e.g., appears in both "must_have" and "nice_to_have"), it should be marked as `required: true` and only appear once in the output.

## Input Format

The input will be a JSON object with the following structure:

```json
{
  "requirements": {
    "must_have": [
      "Work experience as a lead web developer and technical architect",
      "Expertise with .NET Core (C#)",
      "Expertise with Azure",
      "Experience with React w/ Typescript"
    ],
    "nice_to_have": [
      "Strong knowledge of web application development using enterprise-grade technologies",
      "Experience implementing or utilizing Continuous Integration/Continuous Deployment (CI/CD) practices with platforms like Azure DevOps",
      "REST service development and methodologies"
    ]
  }
}
```

## Output Format

Return the analysis in JSON format using the following structure, providing a flat list of technology objects, each including a `required` boolean field, plus a separate array of the most essential technology names for the job:

```json
{
  "technologies": [
    { "name": "TechnologyName1", "required": true },
    { "name": "TechnologyName2", "required": false },
    { "name": "TechnologyName3", "required": true }
  ],
  "main_technologies": ["TechnologyName1", "TechnologyName3"]
}
```

### Main Technologies Selection Criteria

The `main_technologies` array should contain up to 10 of the most essential technology names (as strings) for performing the job, selected using the following priority order:

1. **Required technologies first**: Prioritize technologies from the `must_have` requirements
2. **Core job function technologies**: Focus on technologies that are central to the primary job responsibilities (e.g., for a web developer role, prioritize programming languages, frameworks, and databases over auxiliary tools)
3. **Frequency and emphasis**: Consider technologies that appear multiple times or are emphasized in the requirements
4. **Foundational technologies**: Include technologies that are fundamental to the role (programming languages, primary frameworks, core databases)

If there are fewer than 10 required technologies, include the most relevant ones available. The `main_technologies` should contain only the names (as strings) of technologies that also appear in the `technologies` array.

## Example

Given the input JSON:

```json
{
  "requirements": {
    "must_have": [
      "Expertise with .NET Core (C#)",
      "Experience with React w/ Typescript",
      "Experience with SQL and NoSQL DBs (SQL & Cosmos preferred)",
      "Experience with DevOps (Azure DevOps preferred)"
    ],
    "nice_to_have": [
      "Strong knowledge of web application development using enterprise-grade technologies",
      "Experience implementing CI/CD practices with platforms like Azure DevOps",
      "REST service development and methodologies"
    ]
  }
}
```

The expected output would be:

```json
{
  "technologies": [
    { "name": ".NET", "required": true },
    { "name": "C#", "required": true },
    { "name": "React", "required": true },
    { "name": "TypeScript", "required": true },
    { "name": "Microsoft SQL Server", "required": true },
    { "name": "Azure Cosmos DB", "required": true },
    { "name": "Azure DevOps", "required": true },
    { "name": "REST", "required": false }
  ],
  "main_technologies": [
    ".NET",
    "C#",
    "React",
    "TypeScript",
    "Microsoft SQL Server",
    "Azure Cosmos DB",
    "Azure DevOps"
  ]
}
```

## Technology Normalization Rules

### 1. Consistent Names

Always use the exact canonical name for each technology:

- JavaScript (not Javascript, JS, javascript)
- TypeScript (not Typescript, TS)
- React (not ReactJS, React.js, React JS)
- Angular (normalize all variants like AngularJS, Angular 2+, Angular 17 to this)
- Vue (not VueJS, Vue.js)
- Node.js (not NodeJS, Node)
- Go (not Golang)
- PostgreSQL (not Postgres, PG)
- Microsoft SQL Server (not MSSQL, SQL Server)
- MongoDB (not Mongo)
- Amazon Web Services (not just AWS)
- Google Cloud Platform (not just GCP)
- Microsoft Azure (not just Azure)
- .NET (normalize variants like .NET Core, .NET Framework, .NET 8 to this)
- Python (normalize variants like Python 2, Python 3 to this)

### 2. Extract Core Technology Names

Extract only the core technology name, not verbose descriptions:

- Django (not Django REST Framework)
- Spring (not Spring Boot or Spring Framework)
- ELK (not ELK Stack)

### 3. Remove Redundant Terms

- Suffixes like Framework, Library, Platform should be removed unless part of the official name (e.g., Google Cloud Platform is kept, but React Library becomes React)
- Version numbers must always be removed (e.g., Kubernetes 1.28 becomes Kubernetes, Python 3.11 becomes Python, Angular 16 becomes Angular)

### 4. AI/ML Specific Libraries

Use these exact names:

- TensorFlow (not Tensorflow)
- PyTorch (not Pytorch)
- scikit-learn (not Scikit-learn or SkLearn)
- spaCy (not Spacy)

### 5. Database Systems

Use these canonical names:

- PostgreSQL (not Postgres)
- MySQL (as is)
- Microsoft SQL Server (not MSSQL or SQL Server)
- SQLite (as is)
- MongoDB (not Mongo)

### 6. Cloud Providers

Use full names:

- Amazon Web Services (not AWS)
- Microsoft Azure (not just Azure)
- Google Cloud Platform (not GCP or Google Cloud)

### 7. DevOps Tools

- Docker (as is)
- Kubernetes (not K8s)
- Terraform (as is)
- Git (not git)
- GitHub (not Github or github)

## JSON Requirements Data to Analyze

{requirements_json}
