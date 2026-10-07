# Only remove pairs that were saved in the recovery snapshot.
PREFIX dc: <http://uow.edu.au/isit906/thestar/digitalcare#>
DELETE { ?guide dc:isReviewedBy ?reviewer . }
WHERE {
  VALUES (?guide ?reviewer) { __PAIRS__ }
  ?guide dc:isReviewedBy ?reviewer .
}
