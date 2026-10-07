PREFIX dc: <http://uow.edu.au/isit906/thestar/digitalcare#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>

# Internal template: GUIDE and REVIEWER are validated IRIs from the dropdowns.
# This update creates exactly one relationship and no rdf:type/new-record triple.
# Recheck both types and duplicate status inside the update, after validation.
INSERT { <__GUIDE__> dc:isReviewedBy <__REVIEWER__> . }
WHERE {
  <__GUIDE__> rdf:type/rdfs:subClassOf* dc:Guide .
  <__REVIEWER__> rdf:type/rdfs:subClassOf* dc:HealthcareProfessional .
  FILTER NOT EXISTS { <__GUIDE__> dc:isReviewedBy <__REVIEWER__> . }
}

