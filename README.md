# HBR Feedcast

This project uses the [serverless framework](https://serverless.com) to build an application using AWS native services that does the following:

- Polls the HBR IdeaCast RSS feed on a regular interval and stores information about the podcasts in a DynamoDB table
- Each podcast's content description is sent to Comprehend for entity analysis
- The results from Comprehend are stored in an Elasticsearch cluster
- A Lambda powered Alexa skill is also created which allows users to query the cluster or DynamoDB table with questions related to the content